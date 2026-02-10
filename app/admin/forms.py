from flask_wtf import FlaskForm
from flask_wtf.file import FileAllowed, FileField
from wtforms import (
    BooleanField,
    HiddenField,
    IntegerField,
    PasswordField,
    SelectField,
    StringField,
    SubmitField,
    TextAreaField,
)
from wtforms.validators import DataRequired, EqualTo, Length, NumberRange, Optional

from ..auth.forms import _validate_password_strength
from ..models import LANGUAGE_CHOICES


class BookForm(FlaskForm):
    title = StringField(
        "Title",
        validators=[DataRequired(), Length(max=500)],
        render_kw={"placeholder": "Book title"},
    )
    author = HiddenField(
        "Author",
        validators=[DataRequired(), Length(max=500)],
    )
    description = TextAreaField(
        "Description",
        validators=[Optional(), Length(max=5000)],
        render_kw={"placeholder": "Book description", "rows": 4},
    )
    language = SelectField(
        "Language",
        validators=[DataRequired()],
        choices=LANGUAGE_CHOICES,
        default="en",
    )
    publication_year = IntegerField(
        "Publication Year",
        validators=[Optional(), NumberRange(min=1, max=2100)],
        render_kw={"placeholder": "e.g. 1962"},
    )
    isbn = StringField(
        "ISBN",
        validators=[Optional(), Length(max=20)],
        render_kw={"placeholder": "ISBN"},
    )
    other_identifier = StringField(
        "Other Identifier",
        validators=[Optional(), Length(max=255)],
        render_kw={"placeholder": "Other identifier"},
    )
    dewey_decimal = StringField(
        "Dewey Decimal",
        validators=[Optional(), Length(max=20)],
        render_kw={"placeholder": "e.g. 282.09"},
    )
    loc_classification = StringField(
        "LoC Classification",
        validators=[Optional(), Length(max=50)],
        render_kw={"placeholder": "e.g. BX1751"},
    )
    owned_copies = IntegerField(
        "Owned Copies",
        validators=[DataRequired(), NumberRange(min=1, max=999)],
        default=1,
    )
    watermark_mode = SelectField(
        "Watermark Mode",
        choices=[("standard", "Standard"), ("gentle", "Gentle")],
        default="standard",
    )
    loan_duration_override = IntegerField(
        "Loan Duration Override (days)",
        validators=[Optional(), NumberRange(min=1, max=365)],
        render_kw={"placeholder": "Leave blank for default"},
    )
    is_visible = BooleanField("Visible in catalog", default=True)
    is_disabled = BooleanField("Disabled (no new loans)", default=False)
    restricted_access = BooleanField("Restricted access", default=False)
    tags_text = StringField(
        "Tags",
        validators=[Optional(), Length(max=1000)],
        render_kw={"placeholder": "Comma-separated tags"},
    )
    imprimatur = StringField(
        "Imprimatur",
        validators=[Optional(), Length(max=500)],
        render_kw={"placeholder": "e.g. +John Cardinal O'Hara, Archbishop of Philadelphia"},
    )
    nihil_obstat = StringField(
        "Nihil Obstat",
        validators=[Optional(), Length(max=500)],
        render_kw={"placeholder": "e.g. Rev. John A. Goodwine, S.T.D., Censor Librorum"},
    )
    ecclesiastical_approval_date = StringField(
        "Ecclesiastical Approval Date",
        validators=[Optional(), Length(max=100)],
        render_kw={"placeholder": "e.g. January 15, 1955"},
    )
    master_file = FileField(
        "Master PDF",
        validators=[FileAllowed(["pdf"], "PDF files only.")],
    )
    cover_file = FileField(
        "Cover Image",
        validators=[FileAllowed(["jpg", "jpeg", "png", "webp"], "Image files only.")],
    )
    submit = SubmitField("Save Book")


class LoanExtendForm(FlaskForm):
    days = IntegerField(
        "Days to Add",
        validators=[DataRequired(), NumberRange(min=1, max=365)],
        default=7,
        render_kw={"placeholder": "Number of days"},
    )
    submit = SubmitField("Extend Loan")


class LoanInvalidateForm(FlaskForm):
    reason = StringField(
        "Reason",
        validators=[DataRequired(), Length(max=500)],
        render_kw={"placeholder": "Reason for invalidation"},
    )
    submit = SubmitField("Invalidate Loan")


class UserBlockForm(FlaskForm):
    reason = TextAreaField(
        "Block Reason",
        validators=[DataRequired(), Length(max=1000)],
        render_kw={"placeholder": "Reason for blocking this patron", "rows": 3},
    )
    submit = SubmitField("Block User")


class UserRoleForm(FlaskForm):
    role = SelectField(
        "Role",
        choices=[("patron", "Patron"), ("librarian", "Librarian")],
    )
    submit = SubmitField("Change Role")


class BookSearchForm(FlaskForm):
    class Meta:
        csrf = False

    q = StringField(
        "Search",
        validators=[Optional(), Length(max=200)],
        render_kw={"placeholder": "Search by title or author"},
    )


class LoanSearchForm(FlaskForm):
    class Meta:
        csrf = False

    q = StringField(
        "Search",
        validators=[Optional(), Length(max=200)],
        render_kw={"placeholder": "Search by patron email or book title"},
    )
    status = SelectField(
        "Status",
        choices=[("all", "All"), ("active", "Active"), ("expired", "Expired"), ("returned", "Returned")],
        default="all",
    )


class UserSearchForm(FlaskForm):
    class Meta:
        csrf = False

    q = StringField(
        "Search",
        validators=[Optional(), Length(max=200)],
        render_kw={"placeholder": "Search by email or name"},
    )


class AuditFilterForm(FlaskForm):
    class Meta:
        csrf = False

    action = StringField(
        "Action",
        validators=[Optional(), Length(max=100)],
        render_kw={"placeholder": "Filter by action"},
    )
    date_from = StringField(
        "From",
        validators=[Optional()],
        render_kw={"type": "date"},
    )
    date_to = StringField(
        "To",
        validators=[Optional()],
        render_kw={"type": "date"},
    )


class BookRequestResolveForm(FlaskForm):
    status = SelectField(
        "Decision",
        choices=[
            ("approved", "Approved"),
            ("declined", "Declined"),
            ("fulfilled", "Fulfilled"),
        ],
        validators=[DataRequired()],
    )
    admin_notes = TextAreaField(
        "Admin Notes",
        validators=[Optional(), Length(max=2000)],
        render_kw={"placeholder": "Notes for the patron (optional)", "rows": 3},
    )
    submit = SubmitField("Resolve Request")


class AdminChangePasswordForm(FlaskForm):
    current_password = PasswordField(
        "Current Password",
        validators=[DataRequired()],
    )
    new_password = PasswordField(
        "New Password",
        validators=[DataRequired(), Length(min=8, max=72), _validate_password_strength],
    )
    confirm_password = PasswordField(
        "Confirm New Password",
        validators=[DataRequired(), EqualTo("new_password", message="Passwords must match.")],
    )
    submit = SubmitField("Change Password")


class ReadingListForm(FlaskForm):
    name = StringField(
        "Name",
        validators=[DataRequired(), Length(max=255)],
        render_kw={"placeholder": "Reading list name"},
    )
    description = TextAreaField(
        "Description",
        validators=[Optional(), Length(max=2000)],
        render_kw={"placeholder": "A brief description of this collection", "rows": 4},
    )
    is_public = BooleanField("Public (visible to patrons)", default=True)
    is_featured = BooleanField("Featured (highlighted on collections page)", default=False)
    season = SelectField(
        "Liturgical Season (optional)",
        choices=[
            ("", "-- No season --"),
            ("advent", "Advent"),
            ("christmas", "Christmastide"),
            ("ordinary_early", "Ordinary Time (early)"),
            ("lent", "Lent"),
            ("easter", "Eastertide"),
            ("ordinary_late", "Ordinary Time (late)"),
        ],
        default="",
        validators=[Optional()],
    )
    submit = SubmitField("Save Reading List")


class StagedBookForm(FlaskForm):
    title = StringField("Title", validators=[Optional(), Length(max=500)])
    author = HiddenField("Author", validators=[Optional(), Length(max=500)])
    description = TextAreaField("Description", validators=[Optional(), Length(max=5000)], render_kw={"rows": 4})
    language = SelectField(
        "Language",
        validators=[Optional()],
        choices=[("", "-- Select --")] + LANGUAGE_CHOICES,
        default="en",
    )
    publication_year = IntegerField("Publication Year", validators=[Optional(), NumberRange(min=1, max=2100)])
    isbn = StringField("ISBN", validators=[Optional(), Length(max=20)])
    tags_text = StringField(
        "Tags", validators=[Optional(), Length(max=1000)], render_kw={"placeholder": "Comma-separated tags"}
    )
    submit = SubmitField("Save Changes")
