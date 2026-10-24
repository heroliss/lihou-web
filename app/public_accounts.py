"""Durable visitor upgrades and optional verified-email accounts for LISS."""
import hashlib
import hmac

from flask import abort, current_app, flash, redirect, render_template, request, session, url_for
from flask_login import current_user, login_user, logout_user
from flask_wtf import FlaskForm
from sqlalchemy import func, or_
from sqlalchemy.exc import IntegrityError
from wtforms import SubmitField

from . import db
from .auth.forms import (AccountEmailForm, EmailPasswordResetForm,
                         EmailResetRequestForm, PublicLoginForm, PublicRegistrationForm)
from .models import Role, User
from .transactional_mail import account_link, deliver_account_email, email_accounts_enabled


class ConfirmationForm(FlaskForm):
    submit = SubmitField('重新发送验证邮件')


def _identity_stamp(user):
    credential = f'public-account:{user.id}:{user.password_hash or ""}'
    if user.session_version:
        credential += f':version:{user.session_version}'
    credential = credential.encode('utf-8')
    return hmac.new(current_app.config['SECRET_KEY'].encode('utf-8'),
                    credential, hashlib.sha256).hexdigest()


def refresh_identity_session(user):
    """Keep this browser after a deliberate password change in its own session."""
    if user.is_registered:
        session['public_account_credential'] = _identity_stamp(user)


def validate_identity_session():
    """Invalidate password-account cookies issued before a credential change.

    Visitors retain their historical cookie contract. Legacy password accounts
    without a stamp sign in once to establish the new credential binding.
    """
    # Inactive UserMixin identities report is_authenticated=False too. Inspect
    # the loaded identity before that shortcut so suspension clears old cookies.
    if current_user.is_anonymous:
        return
    if current_user.disabled:
        epoch = current_app.extensions['public_demo'].epoch
        uid = current_user.id
        logout_user()
        session.clear()
        session['public_demo_epoch'] = epoch
        session['disabled_identity'] = uid
        flash('这个身份已被暂停使用。如需恢复，请联系站点管理员。')
        return
    if not current_user.is_registered:
        if session.get('public_guest_version', 0) != current_user.session_version:
            epoch = current_app.extensions['public_demo'].epoch
            logout_user()
            session.clear()
            session['public_demo_epoch'] = epoch
            flash('登录状态已更新，请重新加入。原有内容仍然保留。')
        return
    saved = session.get('public_account_credential')
    if isinstance(saved, str) and hmac.compare_digest(saved.encode('utf-8'),
                                                     _identity_stamp(current_user).encode('ascii')):
        return
    epoch = current_app.extensions['public_demo'].epoch
    logout_user()
    session.clear()
    session['public_demo_epoch'] = epoch
    flash('登录状态已更新，请重新登录。原有账号和内容仍然保留。')


def establish_identity(user):
    """Rotate the signed session without changing the saved account or content."""
    sandbox = current_app.extensions['public_demo']
    session.clear()
    session['public_demo_epoch'] = sandbox.epoch
    session['public_demo_uid'] = str(user.id)
    if user.is_visitor:
        session['public_guest_version'] = user.session_version
    session.permanent = True
    login_user(user, remember=False)
    refresh_identity_session(user)


def destination():
    next_url = request.args.get('next', '')
    return (next_url if next_url.startswith('/') and not next_url.startswith('//')
            and '\\' not in next_url and not any(ord(char) < 32 for char in next_url)
            else url_for('main.index'))


def send_confirmation(user):
    token = user.generate_account_token('confirm-email')
    return deliver_account_email(user.email, '确认你的邮箱', 'auth/email/account_action',
        user=user, heading='确认你的邮箱', action='确认邮箱',
        link=account_link('auth.confirm', token=token),
        explanation='确认后，可以使用这个邮箱登录并找回密码。')


def _delivery_notice(result):
    if result.accepted:
        flash('验证邮件已交给邮件服务，请检查收件箱和垃圾邮件；链接 1 小时内有效。')
    else:
        flash('验证邮件暂未发送成功。账号与原有内容已保留，请稍后重新发送。')


def login():
    if current_user.is_authenticated and current_user.is_registered:
        return redirect(url_for('main.index'))
    guest_content_notice = current_user.is_authenticated and current_user.is_visitor
    form = PublicLoginForm()
    if form.validate_on_submit():
        identifier = form.username.data.strip().lower()
        user = User.query.filter(or_(func.lower(User.username) == identifier,
                                     func.lower(User.email) == identifier),
                                 User.account_kind == 'account').first()
        if user is not None and not user.disabled and user.verify_password(form.password.data):
            establish_identity(user)
            if user.email and not user.confirmed:
                flash('邮箱尚未确认。你仍可继续浏览和保留自己的内容。')
            return redirect(destination())
        flash('邮箱、用户名或密码不正确。')
    return render_template('auth/login.html', form=form, guest_content_notice=guest_content_notice,
                           email_ready=email_accounts_enabled())


def register():
    from .admin import settings_values
    if not settings_values()['registration_open']:
        abort(403, description='站点暂时关闭新账号注册；已有账号仍可正常登录。')
    if session.get('disabled_identity'):
        abort(403, description='这个身份已被暂停使用，请联系站点管理员。')
    if current_user.is_authenticated and current_user.is_registered:
        return redirect(url_for('main.index'))
    sandbox = current_app.extensions['public_demo']
    upgrading_guest = sandbox.participant() and current_user.is_visitor
    email_ready = email_accounts_enabled()
    form = PublicRegistrationForm(email_enabled=email_ready)
    if form.validate_on_submit():
        email = (form.email.data or None) if email_ready else None
        if email:
            sandbox.limit_mail(email)
        ordinary_role = Role.query.filter_by(default=True).first()
        if upgrading_guest:
            user = db.session.get(User, current_user.id)
        else:
            sandbox.check_identity_capacity()
            user = User(role=ordinary_role)
        user.username = form.username.data
        user.email = email
        # Email is optional; a password account without one is fully usable.
        user.confirmed = not bool(email)
        if not upgrading_guest:
            user.name = form.username.data
        user.password = form.password.data
        if form.nickname.data:
            user.name = form.nickname.data
        user.avatar_hash = None
        user.account_kind = 'account'
        user.role = ordinary_role
        db.session.add(user)
        try:
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
            flash('邮箱或用户名已被使用，请登录已有账号，或使用其他邮箱。')
            return render_template('auth/register.html', form=form, upgrading_guest=upgrading_guest,
                                   email_ready=email_ready)
        establish_identity(user)
        if email:
            _delivery_notice(send_confirmation(user))
        else:
            flash('账号已创建，原有内容已保留。' if upgrading_guest else '欢迎加入 LISS。')
        return redirect(destination())
    return render_template('auth/register.html', form=form, upgrading_guest=upgrading_guest,
                           email_ready=email_ready)


def unconfirmed():
    if not current_user.is_authenticated or not current_user.is_registered:
        return redirect(url_for('auth.login'))
    return render_template('auth/unconfirmed.html', form=ConfirmationForm(),
                           email_ready=email_accounts_enabled())


def resend_confirmation():
    if not current_user.is_authenticated or not current_user.is_registered:
        return redirect(url_for('auth.login'))
    form = ConfirmationForm()
    if form.validate_on_submit():
        if not current_user.email:
            return redirect(url_for('auth.change_email_request'))
        if current_user.confirmed:
            flash('这个邮箱已经确认。')
        else:
            current_app.extensions['public_demo'].limit_mail(current_user.email)
            _delivery_notice(send_confirmation(current_user._get_current_object()))
    return redirect(url_for('auth.unconfirmed'))


def confirm(token):
    loaded = User.load_account_token(token, 'confirm-email')
    if loaded and loaded[0].email:
        user, _ = loaded
        user.confirmed = True
        db.session.commit()
        flash('邮箱已确认。可以使用邮箱登录和找回密码。')
    else:
        flash('验证链接无效或已过期，请登录后重新发送。')
    return redirect(url_for('main.index'))


def password_reset_request():
    ready = email_accounts_enabled()
    form = EmailResetRequestForm()
    if form.validate_on_submit() and ready:
        current_app.extensions['public_demo'].limit_mail(form.email.data)
        user = User.query.filter(func.lower(User.email) == form.email.data,
                                 User.account_kind == 'account', User.confirmed.is_(True)).first()
        if user:
            token = user.generate_account_token('reset-password')
            result = deliver_account_email(user.email, '重置密码', 'auth/email/account_action',
                user=user, heading='重置你的密码', action='设置新密码',
                link=account_link('auth.password_reset', token=token),
                explanation='如果不是你发起的请求，请忽略此邮件。密码修改后，本链接立即失效。')
            if not result.accepted:
                current_app.logger.warning('Account recovery email was not accepted by the mail service.')
        flash('找回请求已处理。若邮箱已绑定并验证，我们会尝试发送重置链接；未收到时请稍后重试。')
        return redirect(url_for('auth.login'))
    return render_template('auth/reset_password.html', form=form, email_ready=ready,
                           reset_request=True)


def password_reset(token):
    loaded = User.load_account_token(token, 'reset-password')
    if not loaded or not loaded[0].email or not loaded[0].confirmed:
        flash('重置链接无效或已过期，请重新发起找回请求。')
        return redirect(url_for('auth.password_reset_request'))
    form = EmailPasswordResetForm()
    if form.validate_on_submit():
        user, _ = loaded
        user.password = form.password.data
        db.session.commit()
        flash('密码已更新，请使用新密码登录。')
        return redirect(url_for('auth.login'))
    return render_template('auth/reset_password.html', form=form, email_ready=True,
                           reset_request=False)


def change_email_request():
    if not current_user.is_authenticated or not current_user.is_registered:
        return redirect(url_for('auth.login'))
    form = AccountEmailForm()
    ready = email_accounts_enabled()
    if form.validate_on_submit() and ready:
        user = current_user._get_current_object()
        if user.verify_password(form.password.data):
            current_app.extensions['public_demo'].limit_mail(form.email.data)
            if user.email == form.email.data:
                _delivery_notice(send_confirmation(user))
            else:
                token = user.generate_account_token('change-email', new_email=form.email.data)
                result = deliver_account_email(form.email.data, '确认绑定邮箱', 'auth/email/account_action',
                    user=user, heading='确认绑定这个邮箱', action='确认绑定邮箱',
                    link=account_link('auth.change_email', token=token),
                    explanation='确认后，此邮箱将用于登录和密码找回。确认之前，原邮箱保持有效。')
                _delivery_notice(result)
            return redirect(url_for('auth.unconfirmed'))
        form.password.errors.append('当前密码不正确。')
    return render_template('auth/change_email.html', form=form, email_ready=ready)


def change_email(token):
    loaded = User.load_account_token(token, 'change-email')
    if loaded:
        user, data = loaded
        email = data.get('new_email')
        other = User.query.filter(func.lower(User.email) == email).first() if isinstance(email, str) else None
        if email and len(email) <= 64 and (other is None or other.id == user.id):
            user.email = email
            user.confirmed = True
            user.avatar_hash = hashlib.md5(email.encode()).hexdigest()
            try:
                db.session.commit()
                flash('邮箱已绑定并完成验证。')
                return redirect(url_for('main.index'))
            except IntegrityError:
                db.session.rollback()
    flash('邮箱链接无效、已过期或此邮箱已被使用，请重新申请。')
    return redirect(url_for('main.index'))
