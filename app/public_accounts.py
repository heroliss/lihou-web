"""Password accounts for the public LISS site, without an email dependency."""
from flask import current_app, flash, redirect, render_template, request, session, url_for
from flask_login import current_user, login_user
from sqlalchemy import func

from . import db
from .auth.forms import PublicLoginForm, PublicRegistrationForm
from .models import Role, User


def establish_identity(user):
    """Rotate the signed session without changing the saved account or content."""
    sandbox = current_app.extensions['public_demo']
    session.clear()
    session['public_demo_epoch'] = sandbox.epoch
    session['public_demo_uid'] = str(user.id)
    session.permanent = True
    login_user(user, remember=False)


def destination():
    next_url = request.args.get('next', '')
    return (next_url if next_url.startswith('/') and not next_url.startswith('//')
            and '\\' not in next_url and not any(ord(char) < 32 for char in next_url)
            else url_for('main.index'))


def login():
    if current_user.is_authenticated and current_user.is_registered:
        return redirect(url_for('main.index'))
    guest_content_notice = current_user.is_authenticated and current_user.is_visitor
    form = PublicLoginForm()
    if form.validate_on_submit():
        user = User.query.filter(func.lower(User.username) == form.username.data.lower(),
                                 User.account_kind == 'account').first()
        if user is not None and user.verify_password(form.password.data):
            establish_identity(user)
            return redirect(destination())
        flash('用户名或密码错误。')
    return render_template('auth/login.html', form=form, guest_content_notice=guest_content_notice)


def register():
    if current_user.is_authenticated and current_user.is_registered:
        return redirect(url_for('main.index'))
    sandbox = current_app.extensions['public_demo']
    upgrading_guest = sandbox.participant() and current_user.is_visitor
    form = PublicRegistrationForm()
    if form.validate_on_submit():
        ordinary_role = Role.query.filter_by(default=True).first()
        if upgrading_guest:
            user = db.session.get(User, current_user.id)
        else:
            sandbox.check_identity_capacity()
            user = User(role=ordinary_role)
        # Upgrading the same row retains every post, comment, follow and timestamp.
        user.username = form.username.data
        user.password = form.password.data
        if form.nickname.data:
            user.name = form.nickname.data
        elif not upgrading_guest:
            user.name = form.username.data
        user.email = None
        user.avatar_hash = None
        user.account_kind = 'account'
        user.confirmed = True
        user.role = ordinary_role
        db.session.add(user)
        db.session.commit()
        establish_identity(user)
        flash('账号已创建，原有内容已保留。' if upgrading_guest else '欢迎加入 LISS。')
        return redirect(destination())
    return render_template('auth/register.html', form=form, upgrading_guest=upgrading_guest)
