from flask import abort, flash, redirect, render_template, request, url_for
from flask_login import current_user

from .. import limiter
from ..audit import log_event
from ..models import Book, BookRequest, ReadingList, ReadingListItem, db
from .common import _utcnow, admin_bp, admin_required
from .forms import BookRequestResolveForm, ReadingListForm

# ── Book Requests ─────────────────────────────────────────────────


@admin_bp.route("/requests")
@admin_required
def book_requests():
    status_filter = request.args.get("status", "pending")
    page = request.args.get("page", 1, type=int)

    query = BookRequest.query

    if status_filter and status_filter != "all":
        query = query.filter(BookRequest.status == status_filter)

    query = query.order_by(BookRequest.created_at.desc())
    pagination = query.paginate(page=page, per_page=25, error_out=False)

    # Count pending for the badge
    pending_count = BookRequest.query.filter_by(status="pending").count()

    resolve_form = BookRequestResolveForm()

    return render_template(
        "admin/requests.html",
        requests=pagination.items,
        pagination=pagination,
        status_filter=status_filter,
        pending_count=pending_count,
        resolve_form=resolve_form,
    )


@admin_bp.route("/requests/<int:request_id>/resolve", methods=["POST"])
@admin_required
@limiter.limit("30 per minute")
def book_request_resolve(request_id):
    book_req = db.session.get(BookRequest, request_id)
    if not book_req:
        abort(404)

    form = BookRequestResolveForm()
    if form.validate_on_submit():
        book_req.status = form.status.data
        book_req.admin_notes = form.admin_notes.data.strip() if form.admin_notes.data else None
        book_req.resolved_by = current_user.id
        book_req.resolved_at = _utcnow()
        db.session.commit()

        log_event(
            "book_request_resolved",
            target_type="book_request",
            target_id=book_req.id,
            detail=f'Request for "{book_req.title}" {book_req.status} by admin',
        )
        flash(
            f'Request for "{book_req.title}" has been {book_req.status}.',
            "success",
        )
    else:
        flash("Invalid form submission.", "danger")

    return redirect(url_for("admin.book_requests"))


# ── Reading Lists ─────────────────────────────────────────────────


@admin_bp.route("/reading-lists")
@admin_required
def reading_lists():
    all_lists = ReadingList.query.order_by(ReadingList.name.asc()).all()
    return render_template("admin/reading_lists.html", reading_lists=all_lists)


@admin_bp.route("/reading-lists/new", methods=["GET", "POST"])
@admin_required
@limiter.limit("30 per minute")
def reading_list_new():
    form = ReadingListForm()
    if form.validate_on_submit():
        rl = ReadingList(
            name=form.name.data.strip(),
            description=form.description.data or None,
            is_public=form.is_public.data,
            is_featured=form.is_featured.data,
            season=form.season.data or None,
            created_by=current_user.id,
        )
        db.session.add(rl)
        db.session.commit()

        log_event(
            "reading_list_created",
            target_type="reading_list",
            target_id=rl.id,
            detail=f"Created reading list: {rl.name}",
        )
        flash("Reading list created. You can now add books to it.", "success")
        return redirect(url_for("admin.reading_list_edit", list_id=rl.id))

    return render_template(
        "admin/reading_list_edit.html",
        form=form,
        reading_list=None,
        all_books=[],
    )


@admin_bp.route("/reading-lists/<int:list_id>/edit", methods=["GET", "POST"])
@admin_required
@limiter.limit("30 per minute")
def reading_list_edit(list_id):
    rl = db.session.get(ReadingList, list_id)
    if not rl:
        abort(404)

    form = ReadingListForm(obj=rl)

    # Books available to add (exclude those already in the list)
    existing_book_ids = [item.book_id for item in rl.items]
    all_books = (
        Book.query.filter(~Book.id.in_(existing_book_ids) if existing_book_ids else True).order_by(Book.title).all()
    )

    if form.validate_on_submit():
        rl.name = form.name.data.strip()
        rl.description = form.description.data or None
        rl.is_public = form.is_public.data
        rl.is_featured = form.is_featured.data
        rl.season = form.season.data or None

        # Update positions and notes, process removals
        for item in list(rl.items):
            # Check for removal
            if request.form.get(f"remove_{item.id}"):
                db.session.delete(item)
                continue

            # Update position
            new_pos = request.form.get(f"position_{item.id}", type=int)
            if new_pos is not None:
                item.position = new_pos

            # Update note
            new_note = request.form.get(f"note_{item.id}", "").strip()
            item.note = new_note or None

        # Add new book if selected
        add_book_id = request.form.get("add_book_id", type=int)
        if add_book_id:
            book = db.session.get(Book, add_book_id)
            if book:
                max_pos = max((item.position for item in rl.items), default=0)
                new_item = ReadingListItem(
                    reading_list_id=rl.id,
                    book_id=book.id,
                    position=max_pos + 1,
                )
                db.session.add(new_item)

        db.session.commit()

        log_event(
            "reading_list_updated",
            target_type="reading_list",
            target_id=rl.id,
            detail=f"Updated reading list: {rl.name}",
        )
        flash("Reading list updated.", "success")
        return redirect(url_for("admin.reading_list_edit", list_id=rl.id))

    return render_template(
        "admin/reading_list_edit.html",
        form=form,
        reading_list=rl,
        all_books=all_books,
    )


@admin_bp.route("/reading-lists/<int:list_id>/delete", methods=["POST"])
@admin_required
@limiter.limit("30 per minute")
def reading_list_delete(list_id):
    rl = db.session.get(ReadingList, list_id)
    if not rl:
        abort(404)

    name = rl.name
    db.session.delete(rl)
    db.session.commit()

    log_event(
        "reading_list_deleted", target_type="reading_list", target_id=list_id, detail=f"Deleted reading list: {name}"
    )
    flash(f'Reading list "{name}" deleted.', "success")
    return redirect(url_for("admin.reading_lists"))
