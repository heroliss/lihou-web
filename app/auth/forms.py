from flask import current_app
from flask_wtf import FlaskForm
from wtforms import StringField, PasswordField, BooleanField, SubmitField
from wtforms.validators import DataRequired as Required, Length, Email, Regexp, EqualTo, Optional
from wtforms import ValidationError
from ..models import User
from sqlalchemy import func


class PublicLoginForm(FlaskForm):
    username = StringField('用户名', validators=[Required(), Length(3, 32)])
    password = PasswordField('密码', validators=[Required(), Length(8, 128)])
    submit = SubmitField('登录')


class PublicRegistrationForm(FlaskForm):
    username = StringField('用户名', validators=[Required(), Length(3, 32),
        Regexp(r'^[A-Za-z][A-Za-z0-9_.]*$', message='以字母开头，可包含字母、数字、点和下划线。')])
    nickname = StringField('昵称（可选）', validators=[Optional(), Length(max=64)])
    password = PasswordField('密码', validators=[Required(), Length(8, 128),
        EqualTo('password2', message='两次密码不一致。')])
    password2 = PasswordField('确认密码', validators=[Required()])
    submit = SubmitField('创建账号')

    def validate_username(self, field):
        name = field.data.lower()
        if name.startswith('guest_') or name in {'demo', 'admin', 'administrator', 'system', 'liss'}:
            raise ValidationError('此用户名已保留，请选择其他名称。')
        if User.query.filter(func.lower(User.username) == name).first():
            raise ValidationError('此用户名已被使用。')


class LoginForm(FlaskForm):
    email = StringField('电子邮箱', validators=[Required(), Length(1, 64),
                                             Email()])
    password = PasswordField('密码', validators=[Required()])
    remember_me = BooleanField('记住登录状态')
    submit = SubmitField('登录')


class RegistrationForm(FlaskForm):
    email = StringField('电子邮箱', validators=[Required(), Length(1, 64),
                                           Email()])
    username = StringField('用户名', validators=[
        Required(), Length(1, 64), Regexp('^[A-Za-z][A-Za-z0-9_.]*$', 0,
                                          '用户名只能包含字母,数字，'
                                          '点和下划线')])
    password = PasswordField('密码', validators=[
        Required(), EqualTo('password2', message='两次密码不一致。')])
    password2 = PasswordField('确认密码', validators=[Required()])
    submit = SubmitField('注册')

    def validate_email(self, field):
        if User.query.filter_by(email=field.data).first():
            raise ValidationError('这个邮箱已被注册')

    def validate_username(self, field):
        if User.query.filter_by(username=field.data).first():
            raise ValidationError('此用户名已被使用。')


class ChangePasswordForm(FlaskForm):
    old_password = PasswordField('旧密码', validators=[Required()])
    password = PasswordField('新密码', validators=[
        Required(), EqualTo('password2', message='两次密码不一致。')])
    password2 = PasswordField('确认新密码', validators=[Required()])
    submit = SubmitField('更改密码')

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if current_app.config.get('PUBLIC_DEMO'):
            self.password.validators = [Required(), Length(8, 128),
                                       EqualTo('password2', message='两次密码不一致。')]
            self.old_password.validators = [Required(), Length(8, 128)]


class PasswordResetRequestForm(FlaskForm):
    email = StringField('电子邮箱', validators=[Required(), Length(1, 64),
                                             Email()])
    submit = SubmitField('发送密码重置邮件')


class PasswordResetForm(FlaskForm):
    email = StringField('电子邮箱', validators=[Required(), Length(1, 64),
                                             Email()])
    password = PasswordField('新密码', validators=[
        Required(), EqualTo('password2', message='两次密码不一致。')])
    password2 = PasswordField('确认新密码', validators=[Required()])
    submit = SubmitField('重设密码')

    def validate_email(self, field):
        if User.query.filter_by(email=field.data).first() is None:
            raise ValidationError('不存在这个邮箱')


class ChangeEmailForm(FlaskForm):
    email = StringField('新邮箱', validators=[Required(), Length(1, 64),
                                                 Email()])
    password = PasswordField('密码', validators=[Required()])
    submit = SubmitField('更新邮箱')

    def validate_email(self, field):
        if User.query.filter_by(email=field.data).first():
            raise ValidationError('这个邮箱已被注册')
