import re

from flask_wtf import FlaskForm
from wtforms import BooleanField, PasswordField, RadioField, SelectField, StringField, SubmitField, TextAreaField
from wtforms.validators import DataRequired, Email, EqualTo, Length, Optional, ValidationError

_COMMON_PASSWORDS = {
    "password",
    "12345678",
    "123456789",
    "1234567890",
    "qwerty123",
    "password1",
    "iloveyou",
    "sunshine1",
    "princess1",
    "football1",
    "charlie1",
    "shadow12",
    "monkey123",
    "dragon12",
    "master12",
    "letmein1",
    "trustno1",
    "baseball",
    "superman",
    "michael1",
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
    if not re.search(r"[A-Z]", password):
        raise ValidationError("Password must contain at least one uppercase letter.")
    if not re.search(r"[a-z]", password):
        raise ValidationError("Password must contain at least one lowercase letter.")
    if not re.search(r"[0-9]", password):
        raise ValidationError("Password must contain at least one number.")
    if not password.isprintable():
        raise ValidationError("Password contains invalid characters.")


class LoginForm(FlaskForm):
    email = StringField(
        "Email",
        validators=[DataRequired(), Email(), Length(max=255)],
        render_kw={"placeholder": "Email address", "autofocus": True, "type": "email"},
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
        render_kw={"placeholder": "Email address", "type": "email"},
    )
    password = PasswordField(
        "Password",
        validators=[DataRequired(), Length(min=8, max=72), _validate_password_strength],
        render_kw={"placeholder": "Password (8-72 characters)"},
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
        render_kw={"placeholder": "Email address", "autofocus": True, "type": "email"},
    )
    submit = SubmitField("Send Reset Link")


class ResetPasswordForm(FlaskForm):
    password = PasswordField(
        "New Password",
        validators=[DataRequired(), Length(min=8, max=72), _validate_password_strength],
        render_kw={"placeholder": "New password (8-72 characters)"},
    )
    password_confirm = PasswordField(
        "Confirm New Password",
        validators=[DataRequired(), EqualTo("password", message="Passwords must match.")],
        render_kw={"placeholder": "Confirm new password"},
    )
    submit = SubmitField("Set New Password")


# ── State of life choices (ordered lay → consecrated → formation → ordained) ──

STATE_OF_LIFE_CHOICES = [
    ("", "— Select —"),
    # Laity
    ("Single", "Single"),
    ("Married", "Married"),
    ("Widow / Widower", "Widow / Widower"),
    # Consecrated Life (Individual)
    ("Consecrated Virgin", "Consecrated Virgin"),
    ("Consecrated Widow", "Consecrated Widow"),
    ("Consecrated Hermit", "Consecrated Hermit"),
    # Religious / Third Order Life
    ("Member of a Third Order", "Member of a Third Order"),
    ("Consecrated Member of a Religious Institute", "Consecrated Member of a Religious Institute"),
    # Formation
    ("Seminarian", "Seminarian"),
    # Sacred Orders (ascending)
    ("Married Deacon", "Married Deacon"),
    ("Transitional Deacon", "Transitional Deacon"),
    ("Diocesan Priest", "Diocesan Priest"),
    ("Society Priest", "Society Priest"),
    ("Religious Priest", "Religious Priest"),
    ("Married Priest", "Married Priest"),
    ("Bishop", "Bishop"),
    ("Cardinal", "Cardinal"),
]

BAPTISMAL_STATUS_CHOICES = [
    ("", "— Select —"),
    ("Baptized Catholic", "Baptized Catholic"),
    ("Other Christian", "Other Christian"),
    ("Catechumen", "Catechumen"),
    ("Unbaptized", "Unbaptized"),
]

RITE_CHOICES = [
    ("", "— Select —"),
    ("Roman OF", "Roman (Ordinary Form)"),
    ("Roman EF/TLM", "Roman (Extraordinary Form / TLM)"),
    ("SSPX", "SSPX"),
    ("Eastern Catholic", "Eastern Catholic"),
    ("Old Roman Catholic", "Old Roman Catholic"),
    ("Other", "Other"),
]

# Which state-of-life values require the religious_institute follow-up
_INSTITUTE_STATES = {
    "Member of a Third Order",
    "Consecrated Member of a Religious Institute",
    "Society Priest",
    "Religious Priest",
}

_INSTITUTE_PROMPTS = {
    "Member of a Third Order": "Which Third Order?",
    "Consecrated Member of a Religious Institute": "Which institute, order, or society of apostolic life?",
    "Society Priest": "Which society of apostolic life do you belong to?",
    "Religious Priest": "What institute or order do you belong to?",
}

MONTH_CHOICES = [("", "Month")] + [(str(i), m) for i, m in enumerate(
    ["January", "February", "March", "April", "May", "June",
     "July", "August", "September", "October", "November", "December"], 1
)]

DAY_CHOICES = [("", "Day")] + [(str(i), str(i)) for i in range(1, 32)]


class JoinForm(FlaskForm):
    """Unified registration + membership application form."""

    # ── Section 1: Your Account (skipped if logged in) ──
    first_name = StringField("First Name", validators=[Optional(), Length(max=127)])
    last_name = StringField("Last Name", validators=[Optional(), Length(max=127)])
    email = StringField("Email", validators=[Optional(), Length(max=255)])

    # ── Section 2: About You ──
    birth_month = SelectField("Month", choices=MONTH_CHOICES, validators=[Optional()])
    birth_day = SelectField("Day", choices=DAY_CHOICES, validators=[Optional()])
    state_of_life = SelectField("State of Life", choices=STATE_OF_LIFE_CHOICES, validators=[DataRequired()])
    religious_institute = StringField("Religious Institute", validators=[Optional(), Length(max=255)])

    # ── Section 3: Where You Are ──
    city = StringField("City", validators=[Optional(), Length(max=255)])
    state_province = StringField("State / Province", validators=[Optional(), Length(max=255)])
    country = StringField("Country", validators=[Optional(), Length(max=255)])

    # ── Section 4: Your Faith ──
    baptismal_status = SelectField("Baptismal Status", choices=BAPTISMAL_STATUS_CHOICES, validators=[DataRequired()])
    denomination = StringField("Denomination", validators=[Optional(), Length(max=255)])
    rite = SelectField("Rite", choices=RITE_CHOICES, validators=[Optional()])
    diocese = StringField("Diocese", validators=[Optional(), Length(max=255)])
    parish = StringField("Parish", validators=[Optional(), Length(max=255)])
    sacrament_baptism = BooleanField("Baptism")
    sacrament_confirmation = BooleanField("Confirmation")
    sacrament_eucharist = BooleanField("Holy Eucharist")

    # ── Section 5: Your Application ──
    why_join = TextAreaField(
        "Why do you wish to join the Oratory?",
        validators=[DataRequired(), Length(min=20, max=5000)],
    )
    how_heard = StringField("How did you hear about us?", validators=[Optional(), Length(max=500)])
    profession_of_faith = RadioField(
        "Profession of Faith",
        choices=[("amen", "Amen"), ("no", "No")],
        validators=[DataRequired()],
    )
    submit = SubmitField("Join the Oratory")

    def __init__(self, *args, skip_account_fields=False, **kwargs):
        super().__init__(*args, **kwargs)
        self.skip_account_fields = skip_account_fields
        if skip_account_fields:
            # Remove validators for account fields when user is already logged in
            self.first_name.validators = [Optional()]
            self.last_name.validators = [Optional()]
            self.email.validators = [Optional()]

    def validate(self, extra_validators=None):
        if not self.skip_account_fields:
            # Account fields are required for new users
            if not self.first_name.data or not self.first_name.data.strip():
                self.first_name.errors.append("First name is required.")
                return False
            if not self.last_name.data or not self.last_name.data.strip():
                self.last_name.errors.append("Last name is required.")
                return False
            if not self.email.data or not self.email.data.strip():
                self.email.errors.append("Email is required.")
                return False
            # Validate email format
            from wtforms.validators import Email as EmailValidator
            try:
                EmailValidator()(self, self.email)
            except ValidationError:
                self.email.errors.append("Please enter a valid email address.")
                return False

        # Run standard WTForms validation for remaining fields
        rv = super().validate(extra_validators=extra_validators)

        # Conditional: religious_institute required for certain states of life
        if self.state_of_life.data in _INSTITUTE_STATES:
            if not self.religious_institute.data or not self.religious_institute.data.strip():
                prompt = _INSTITUTE_PROMPTS.get(self.state_of_life.data, "Please specify.")
                self.religious_institute.errors.append(prompt)
                rv = False

        # Conditional: denomination required for "Other Christian"
        if self.baptismal_status.data == "Other Christian":
            if not self.denomination.data or not self.denomination.data.strip():
                self.denomination.errors.append("Please enter your denomination.")
                rv = False

        return rv
