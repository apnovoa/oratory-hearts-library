from flask_wtf import FlaskForm
from wtforms import StringField, SelectField
from wtforms.validators import Length


class CatalogSearchForm(FlaskForm):
    class Meta:
        csrf = False          # GET form, no mutation

    q = StringField("Search", validators=[Length(max=200)])
    tag = SelectField("Tag", choices=[("", "All Tags")], coerce=str)
    language = SelectField("Language", choices=[("", "All Languages")], coerce=str)
    availability = SelectField(
        "Availability",
        choices=[("", "All"), ("available", "Available Now"), ("unavailable", "Checked Out")],
        coerce=str,
    )
    sort = SelectField(
        "Sort",
        choices=[
            ("title", "Title A\u2013Z"),
            ("author", "Author A\u2013Z"),
            ("recent", "Recently Added"),
            ("available", "Availability"),
        ],
        coerce=str,
        default="title",
    )
