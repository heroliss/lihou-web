from flask_wtf import FlaskForm
from wtforms import StringField, PasswordField, BooleanField, SubmitField
from wtforms.validators import Required, Length, Email, Regexp, EqualTo
from wtforms import ValidationError
from ..models import User


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
        Required(), EqualTo('password2', message='我靠不匹配')])
    password2 = PasswordField('确认密码', validators=[Required()])
    submit = SubmitField('注册')

    def validate_email(self, field):
        if User.query.filter_by(email=field.data).first():
            raise ValidationError('这个邮箱已被注册')

    def validate_username(self, field):
        if User.query.filter_by(username=field.data).first():
            raise ValidationError('用户名被别人占了')


class ChangePasswordForm(FlaskForm):
    old_password = PasswordField('旧密码', validators=[Required()])
    password = PasswordField('新密码', validators=[
        Required(), EqualTo('password2', message='我靠，俩个密码不一致')])
    password2 = PasswordField('确认新密码', validators=[Required()])
    submit = SubmitField('更改密码')


class PasswordResetRequestForm(FlaskForm):
    email = StringField('电子邮箱', validators=[Required(), Length(1, 64),
                                             Email()])
    submit = SubmitField('发送密码重置邮件')


class PasswordResetForm(FlaskForm):
    email = StringField('电子邮箱', validators=[Required(), Length(1, 64),
                                             Email()])
    password = PasswordField('新密码', validators=[
        Required(), EqualTo('password2', message='俩个密码不匹配(~_~|||)')])
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
