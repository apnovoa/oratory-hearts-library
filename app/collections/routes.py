"""Public-facing routes for curated reading-list collections."""

from flask import Blueprint, render_template, abort
from flask_login import login_required

from ..models import ReadingList, db
from ..liturgical import get_current_season, get_season_display_name, get_season_description

collections_bp = Blueprint("collections", __name__)


@collections_bp.route("/collections")
@login_required
def index():
    """List all public reading lists, highlighting seasonal ones."""
    current_season = get_current_season()
    season_name = get_season_display_name(current_season)
    season_description = get_season_description(current_season)

    # Seasonal lists that match the current liturgical season
    seasonal_lists = (
        ReadingList.query
        .filter_by(is_public=True, season=current_season)
        .order_by(ReadingList.name.asc())
        .all()
    )

    # Featured lists (non-seasonal or different season)
    featured_lists = (
        ReadingList.query
        .filter(
            ReadingList.is_public == True,   # noqa: E712
            ReadingList.is_featured == True,  # noqa: E712
            db.or_(
                ReadingList.season != current_season,
                ReadingList.season == None,  # noqa: E711
            ),
        )
        .order_by(ReadingList.name.asc())
        .all()
    )

    # All other public lists
    exclude_ids = [rl.id for rl in seasonal_lists + featured_lists]
    other_lists = (
        ReadingList.query
        .filter(
            ReadingList.is_public == True,  # noqa: E712
            ~ReadingList.id.in_(exclude_ids) if exclude_ids else True,
        )
        .order_by(ReadingList.name.asc())
        .all()
    )

    return render_template(
        "collections/index.html",
        seasonal_lists=seasonal_lists,
        featured_lists=featured_lists,
        other_lists=other_lists,
        current_season=current_season,
        season_name=season_name,
        season_description=season_description,
    )


@collections_bp.route("/collections/<public_id>")
@login_required
def detail(public_id):
    """View a single reading list with its books."""
    reading_list = ReadingList.query.filter_by(
        public_id=public_id, is_public=True
    ).first_or_404()

    return render_template(
        "collections/detail.html",
        reading_list=reading_list,
    )
