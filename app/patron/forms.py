from flask_wtf import FlaskForm
from wtforms import StringField, PasswordField, TextAreaField, SelectField, SubmitField
from wtforms.validators import DataRequired, Length, Optional, EqualTo, ValidationError

from ..auth.forms import _validate_password_strength


class ProfileForm(FlaskForm):
    display_name = StringField(
        "Display Name",
        validators=[DataRequired(), Length(min=2, max=255)],
    )
    birth_month = SelectField(
        "Birth Month",
        coerce=int,
        choices=[
            (0, "-- Month --"),
            (1, "January"), (2, "February"), (3, "March"),
            (4, "April"), (5, "May"), (6, "June"),
            (7, "July"), (8, "August"), (9, "September"),
            (10, "October"), (11, "November"), (12, "December"),
        ],
        validators=[Optional()],
    )
    birth_day = SelectField(
        "Birth Day",
        coerce=int,
        choices=[(0, "-- Day --")] + [(d, str(d)) for d in range(1, 32)],
        validators=[Optional()],
    )
    current_password = PasswordField(
        "Current Password",
        validators=[Optional()],
    )
    new_password = PasswordField(
        "New Password",
        validators=[Optional(), Length(min=8, max=128), _validate_password_strength],
    )
    confirm_password = PasswordField(
        "Confirm New Password",
        validators=[Optional(), EqualTo("new_password", message="Passwords must match.")],
    )
    submit = SubmitField("Update Profile")


class BookRequestForm(FlaskForm):
    title = StringField(
        "Book Title",
        validators=[DataRequired(), Length(min=1, max=500)],
        render_kw={"placeholder": "Title of the book you would like added"},
    )
    author = StringField(
        "Author",
        validators=[Optional(), Length(max=500)],
        render_kw={"placeholder": "Author name (if known)"},
    )
    reason = TextAreaField(
        "Reason",
        validators=[Optional(), Length(max=2000)],
        render_kw={
            "placeholder": "Why would you like this book added to the library?",
            "rows": 4,
        },
    )
    submit = SubmitField("Submit Request")


class BookNoteForm(FlaskForm):
    content = TextAreaField(
        "My Notes",
        validators=[DataRequired(), Length(min=1, max=5000)],
        render_kw={
            "placeholder": "Write your personal notes about this book...",
            "rows": 4,
        },
    )
    submit = SubmitField("Save Note")
