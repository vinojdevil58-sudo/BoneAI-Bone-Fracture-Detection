        if q:
            rows = conn.execute(
                """SELECT * FROM scans WHERE patient_code LIKE ? OR patient_name LIKE ?
                   OR predicted_class LIKE ? ORDER BY id DESC LIMIT 250""",
                (f"%{q}%", f"%{q}%", f"%{q}%")
            ).fetchall()
        else:
            rows = conn.execute("SELECT * FROM scans ORDER BY id DESC LIMIT 250").fetchall()
    return render_template("records.html", scans=rows, q=q)

@app.route("/reports")
@login_required
def reports():
    if session.get("role") == "patient":
        return redirect(url_for("patient_history"))
    with db() as conn:
        scans = conn.execute("SELECT * FROM scans WHERE status='complete' ORDER BY id DESC LIMIT 250").fetchall()
    return render_template("reports.html", scans=scans)

@app.route("/report/<int:scan_id>/pdf")
@login_required
def report_pdf(scan_id):
    with db() as conn:
        scan = conn.execute("SELECT * FROM scans WHERE id=?", (scan_id,)).fetchone()
    if not scan:
        flash("Scan not found.", "warning")
        return redirect(url_for("reports"))
    if not scan_access_allowed(scan):
        flash("You can only download your own report.", "danger")
        return redirect(url_for("patient_history"))
    try:
        from reportlab.lib.pagesizes import letter
        from reportlab.pdfgen import canvas
        from io import BytesIO
        buf = BytesIO()
        c = canvas.Canvas(buf, pagesize=letter)
        c.setFont("Helvetica-Bold", 18)
        c.drawString(55, 745, "BoneAI - AI Prediction Report")
        c.setFont("Helvetica", 11)
        lines = [
            f"Scan ID: {scan['id']}",
            f"Patient code: {scan['patient_code'] or 'Not provided'}",
            f"Patient name: {scan['patient_name'] or 'Not provided'}",
            f"Date: {scan['created_at']}",
            f"Status: {scan['status']}",
            f"Model output: {scan['predicted_class'] or 'Not available'}",
            f"Confidence: {scan['confidence'] if scan['confidence'] is not None else 'Not available'}",
            "",
            "Important: This is an AI prediction from an educational prototype, not a diagnosis.",
            "A qualified medical professional must review the original image and findings."
        ]
        y = 710
        for line in lines:
            c.drawString(55, y, line[:105])
            y -= 22
        c.save()
        buf.seek(0)
        from flask import send_file
        return send_file(buf, as_attachment=True, download_name=f"boneai_report_{scan_id}.pdf", mimetype="application/pdf")
    except Exception as exc:
        flash(f"Could not create PDF report: {exc}", "danger")
        return redirect(url_for("reports"))

@app.route("/uploads/<path:filename>")
@login_required
def uploaded_image(filename):
    # image_name stores the Supabase object path, not a local filename.
    with db() as conn:
        scan = conn.execute("SELECT * FROM scans WHERE image_name=?", (filename,)).fetchone()
    if not scan:
        abort(404)
    if not scan_access_allowed(scan):
        abort(403)
    try:
        return redirect(create_xray_signed_url(scan["image_name"]), code=302)
    except Exception:
        app.logger.exception("Could not create private Supabase signed URL")
        abort(503)

@app.route("/results/<path:filename>")
@login_required
def result_image(filename):
    with db() as conn:
        scan = conn.execute("SELECT * FROM scans WHERE result_image=?", (filename,)).fetchone()
    if not scan:
        abort(404)
    if not scan_access_allowed(scan):
        abort(403)
    return send_from_directory(RESULT_DIR, filename)

@app.route("/assistant")
@login_required
def assistant():
    if session.get("role") == "patient":
        return redirect(url_for("patient_dashboard"))
    return render_template("assistant.html")

@app.route("/settings")
@login_required
def settings():
    if session.get("role") == "patient":
        return redirect(url_for("patient_dashboard"))
    return render_template("settings.html")

@app.route("/api/health")
def health():
    return jsonify({"status": "ok", "model_file_present": MODEL_PATH.is_file(), "supabase_configured": bool(os.getenv("SUPABASE_URL") and os.getenv("SUPABASE_SECRET_KEY"))})

@app.errorhandler(403)
def forbidden(_):
    return "You do not have permission to access this file.", 403

@app.errorhandler(404)
def not_found(_):
    return render_template("404.html"), 404

init_db()

if __name__ == "__main__":
    # Local development only; use a production WSGI server when deployed.
    app.run(host="127.0.0.1", port=5000, debug=False)
