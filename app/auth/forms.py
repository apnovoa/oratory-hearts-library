import re

from flask_wtf import FlaskForm
from wtforms import StringField, PasswordField, BooleanField, SubmitField
from wtforms.validators import DataRequired, Email, Length, EqualTo, ValidationError


_COMMON_PASSWORDS = {
    "password", "12345678", "123456789", "1234567890", "qwerty123",
    "password1", "iloveyou", "sunshine1", "princess1", "football1",
    "charlie1", "shadow12", "monkey123", "dragon12", "master12",
    "letmein1", "trustno1", "baseball", "superman", "michael1",
}


def _validate_password_strength(form, field):
    password = field.data
    if not password or len(password) < 8:
        return  # Length validator handles this
    if password.lower() in _COMMON_PASSWORDS:
        raise ValidationError("This password is too common. Please choose a stronger password.")
    # Reject passwords that are mostly non-alphanumeric (SQL injection, code, etc.)
    alnum_count = sum(1 for c in password if c.isalnum())
    if alnum_count < len(password) * 0.5:
        raise ValidationError("Password must be at least half letters or numbers.")
    if not re.search(r'[A-Z]', password):
        raise ValidationError("Password must contain at least one uppercase letter.")
    if not re.search(r'[a-z]', password):
        raise ValidationError("Password must contain at least one lowercase letter.")
    if not re.search(r'[0-9]', password):
        raise ValidationError("Password must contain at least one number.")
    if not password.isprintable():
        raise ValidationError("Password contains invalid characters.")


class LoginForm(FlaskForm):
    email = StringField(
        "Email",
        validators=[DataRequired(), Email(), Length(max=255)],
        render_kw={"placeholder": "Email address", "autofocus": True},
    )
    password = PasswordField(
        "Password",
        validators=[DataRequired()],
        render_kw={"placeholder": "Password"},
    )
    remember_me = BooleanField("Remember me")
    submit = SubmitField("Sign In")


class RegistrationForm(FlaskForm):
    first_name = StringField(
        "First Name",
        validators=[DataRequired(), Length(min=1, max=127)],
        render_kw={"placeholder": "First name", "autofocus": True},
    )
    last_name = StringField(
        "Last Name",
        validators=[DataRequired(), Length(min=1, max=127)],
        render_kw={"placeholder": "Last name"},
    )
    email = StringField(
        "Email",
        validators=[DataRequired(), Email(), Length(max=255)],
        render_kw={"placeholder": "Email address"},
    )
    password = PasswordField(
        "Password",
        validators=[DataRequired(), Length(min=8, max=32), _validate_password_strength],
        render_kw={"placeholder": "Password (8–32 characters)"},
    )
    password_confirm = PasswordField(
        "Confirm Password",
        validators=[DataRequired(), EqualTo("password", message="Passwords must match.")],
        render_kw={"placeholder": "Confirm password"},
    )
    submit = SubmitField("Create Account")

class RequestPasswordResetForm(FlaskForm):
    email = StringField(
        "Email",
        validators=[DataRequired(), Email(), Length(max=255)],
        render_kw={"placeholder": "Email address", "autofocus": True},
    )
    submit = SubmitField("Send Reset Link")


class ResetPasswordForm(FlaskForm):
    password = PasswordField(
        "New Password",
        validators=[DataRequired(), Length(min=8, max=32), _validate_password_strength],
        render_kw={"placeholder": "New password (8–32 characters)"},
    )
    password_confirm = PasswordField(
        "Confirm New Password",
        validators=[DataRequired(), EqualTo("password", message="Passwords must match.")],
        render_kw={"placeholder": "Confirm new password"},
    )
    submit = SubmitField("Set New Password")
