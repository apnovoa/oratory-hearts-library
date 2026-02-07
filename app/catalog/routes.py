import re

from flask import Blueprint, render_template, redirect, request, url_for, abort, current_app, send_from_directory
from flask_login import login_required, current_user
from sqlalchemy import text

from ..models import Book, Tag, Loan, Favorite, BookNote, db
from .forms import CatalogSearchForm
from .helpers import get_related_books


def _sanitize_fts5_query(q):
    """Strip FTS5 query syntax to prevent query injection."""
    # Remove FTS5 operators and special characters
    q = re.sub(r'["\(\)\*\{\}]', ' ', q)
    # Remove boolean operators as standalone words
    q = re.sub(r'\b(AND|OR|NOT|NEAR)\b', ' ', q, flags=re.IGNORECASE)
    # Remove column filter syntax (word followed by colon)
    q = re.sub(r'\w+:', ' ', q)
    # Collapse whitespace
    q = re.sub(r'\s+', ' ', q).strip()
    if not q:
        return None
    # Wrap each remaining token in double quotes for literal matching
    tokens = q.split()
    return ' '.join(f'"{t}"' for t in tokens)

catalog_bp = Blueprint("catalog", __name__)

ITEMS_PER_PAGE = 20

# Language code -> display name mapping
LANGUAGE_LABELS = {
    "en": "English",
    "la": "Latin",
    "es": "Spanish",
    "fr": "French",
    "it": "Italian",
    "de": "German",
    "pt": "Portuguese",
    "pl": "Polish",
}


@catalog_bp.route("/")
def index():
    if current_user.is_authenticated:
        return redirect(url_for("catalog.browse"))
    return render_template("splash.html")


@catalog_bp.route("/catalog")
@login_required
def browse():
    form = CatalogSearchForm(request.args)

    # Populate dynamic choices from the database
    tags = Tag.query.order_by(Tag.name).all()
    form.tag.choices = [("", "All Tags")] + [(t.name, t.name) for t in tags]

    languages = (
        db.session.query(Book.language)
        .filter(Book.is_visible == True, Book.is_disabled == False)  # noqa: E712
        .distinct()
        .order_by(Book.language)
        .all()
    )
    form.language.choices = [("", "All Languages")] + [
        (lang[0], LANGUAGE_LABELS.get(lang[0], lang[0])) for lang in languages
    ]

    # Base query: visible and not disabled
    query = Book.query.filter(
        Book.is_visible == True,   # noqa: E712
        Book.is_disabled == False,  # noqa: E712
    )

    # Search by title or author (FTS5 with LIKE fallback)
    search_query = form.q.data
    if search_query:
        search_query = _sanitize_fts5_query(search_query)
    if search_query:
        try:
            fts_results = db.session.execute(
                text("SELECT rowid FROM books_fts WHERE books_fts MATCH :q"),
                {"q": search_query},
            )
            matching_ids = [row[0] for row in fts_results]
            if matching_ids:
                query = query.filter(Book.id.in_(matching_ids))
            else:
                # FTS returned no results — fall back to LIKE
                pattern = f"%{search_query}%"
                query = query.filter(
                    db.or_(
                        Book.title.ilike(pattern),
                        Book.author.ilike(pattern),
                    )
                )
        except Exception:
            # FTS5 not available — fall back to LIKE
            pattern = f"%{search_query}%"
            query = query.filter(
                db.or_(
                    Book.title.ilike(pattern),
                    Book.author.ilike(pattern),
                )
            )

    # Filter by tag
    tag_filter = form.tag.data
    if tag_filter:
        query = query.filter(Book.tags.any(Tag.name == tag_filter))

    # Filter by language
    language_filter = form.language.data
    if language_filter:
        query = query.filter(Book.language == language_filter)

    # Filter by availability
    availability_filter = form.availability.data
    if availability_filter == "available":
        # Subquery: books with active loan count < owned_copies
        active_loans = (
            db.session.query(
                Loan.book_id,
                db.func.count(Loan.id).label("loan_count"),
            )
            .filter(Loan.is_active == True)  # noqa: E712
            .group_by(Loan.book_id)
            .subquery()
        )
        query = query.outerjoin(active_loans, Book.id == active_loans.c.book_id).filter(
            db.or_(
                active_loans.c.loan_count == None,  # noqa: E711
                active_loans.c.loan_count < Book.owned_copies,
            ),
            Book.master_filename != None,  # noqa: E711
        )
    elif availability_filter == "unavailable":
        active_loans = (
            db.session.query(
                Loan.book_id,
                db.func.count(Loan.id).label("loan_count"),
            )
            .filter(Loan.is_active == True)  # noqa: E712
            .group_by(Loan.book_id)
            .subquery()
        )
        query = query.join(active_loans, Book.id == active_loans.c.book_id).filter(
            active_loans.c.loan_count >= Book.owned_copies,
        )

    # Sort
    sort_value = form.sort.data or "title"
    if sort_value == "author":
        query = query.order_by(Book.author.asc(), Book.title.asc())
    elif sort_value == "recent":
        query = query.order_by(Book.created_at.desc())
    elif sort_value == "available":
        # Available books first (those with a master file), then alphabetical
        query = query.order_by(
            db.case(
                (Book.master_filename != None, 0),  # noqa: E711
                else_=1,
            ),
            Book.title.asc(),
        )
    else:
        # Default: title A-Z
        query = query.order_by(Book.title.asc())

    page = request.args.get("page", 1, type=int)
    pagination = query.paginate(page=page, per_page=ITEMS_PER_PAGE, error_out=False)

    if pagination.page > pagination.pages and pagination.pages > 0:
        return redirect(url_for("catalog.browse", page=pagination.pages))

    # Determine whether any search/filter is active (controls shelf visibility)
    has_active_search = bool(
        form.q.data or form.tag.data or form.language.data or form.availability.data
    )

    # New Arrivals: 6 most recently added books
    new_arrivals = []
    if not has_active_search:
        new_arrivals = (
            Book.query.filter(
                Book.is_visible == True,   # noqa: E712
                Book.is_disabled == False,  # noqa: E712
            )
            .order_by(Book.created_at.desc())
            .limit(6)
            .all()
        )

    # Staff Picks: featured books
    featured_books = []
    if not has_active_search:
        featured_books = (
            Book.query.filter(
                Book.is_featured == True,   # noqa: E712
                Book.is_visible == True,    # noqa: E712
                Book.is_disabled == False,  # noqa: E712
            )
            .order_by(Book.created_at.desc())
            .limit(6)
            .all()
        )

    return render_template(
        "catalog/index.html",
        form=form,
        books=pagination.items,
        pagination=pagination,
        current_sort=sort_value,
        new_arrivals=new_arrivals,
        featured_books=featured_books,
        has_active_search=has_active_search,
    )


@catalog_bp.route("/catalog/<public_id>")
@login_required
def detail(public_id):
    book = Book.query.filter_by(public_id=public_id).first_or_404()

    if not book.is_visible or book.is_disabled:
        abort(404)

    # Check if the current patron has an active loan for this book
    patron_has_loan = False
    patron_on_waitlist = False
    is_favorited = False
    patron_note = None
    if current_user.role == "patron":
        patron_has_loan = Loan.query.filter(
            Loan.book_id == book.id,
            Loan.user_id == current_user.id,
            Loan.is_active == True,  # noqa: E712
        ).first() is not None

        from ..models import WaitlistEntry
        patron_on_waitlist = WaitlistEntry.query.filter(
            WaitlistEntry.book_id == book.id,
            WaitlistEntry.user_id == current_user.id,
            WaitlistEntry.is_fulfilled == False,  # noqa: E712
        ).first() is not None

        is_favorited = Favorite.query.filter_by(
            user_id=current_user.id,
            book_id=book.id,
        ).first() is not None

        patron_note = BookNote.query.filter_by(
            user_id=current_user.id,
            book_id=book.id,
        ).first()

    related_books = get_related_books(book, limit=4)

    # Expected availability date: earliest due_at among active loans
    next_available_date = None
    if not book.is_available:
        earliest_loan = (
            Loan.query
            .filter(
                Loan.book_id == book.id,
                Loan.is_active == True,  # noqa: E712
            )
            .order_by(Loan.due_at.asc())
            .first()
        )
        if earliest_loan:
            next_available_date = earliest_loan.due_at

    return render_template(
        "catalog/detail.html",
        book=book,
        patron_has_loan=patron_has_loan,
        patron_on_waitlist=patron_on_waitlist,
        is_favorited=is_favorited,
        patron_note=patron_note,
        related_books=related_books,
        next_available_date=next_available_date,
    )


@catalog_bp.route("/covers/<filename>")
@login_required
def serve_cover(filename):
    book = Book.query.filter_by(cover_filename=filename).first()
    if not book:
        abort(404)
    return send_from_directory(current_app.config["COVER_STORAGE"], filename)


@catalog_bp.route("/policy")
def policy():
    return render_template("catalog/policy.html")


@catalog_bp.route("/donate")
def donate():
    return render_template("catalog/donate.html")
