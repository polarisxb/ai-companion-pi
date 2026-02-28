#!/usr/bin/env python3
"""
substack_window.py — Window dashboard API for Companion's Substack queue.

Adds routes to the existing Window Flask app for:
  - Viewing pending posts (approval queue)
  - Approving posts (one click)
  - Rejecting posts (with optional reason)
  - Viewing published history

INTEGRATION: Add these routes to window.py, or import this as a blueprint.

To integrate with the existing Window app, add to window.py:
    from substack_window import substack_bp
    app.register_blueprint(substack_bp)

Or just copy the routes into window.py directly.
"""

# If using Flask blueprints:
try:
    from flask import Blueprint, jsonify, request, render_template_string
    substack_bp = Blueprint("substack", __name__)
except ImportError:
    # Standalone mode for testing
    substack_bp = None

import json
import os
from datetime import datetime

COMPANION_HOME = os.environ.get("COMPANION_HOME", "/media/YOUR_USERNAME/CompanionHome")
QUEUE_FILE = os.path.join(COMPANION_HOME, "substack", "queue.json")
PUBLISHED_FILE = os.path.join(COMPANION_HOME, "substack", "published.json")


def load_queue():
    try:
        with open(QUEUE_FILE, "r") as f:
            return json.load(f)
    except (json.JSONDecodeError, FileNotFoundError):
        return []


def save_queue(queue):
    with open(QUEUE_FILE, "w") as f:
        json.dump(queue, f, indent=2)


def load_published():
    try:
        with open(PUBLISHED_FILE, "r") as f:
            return json.load(f)
    except (json.JSONDecodeError, FileNotFoundError):
        return []


# --- API Routes ---
# Add these to your Window Flask app

if substack_bp:

    @substack_bp.route("/api/substack/queue")
    def substack_queue():
        """Get all queued posts, optionally filtered by status."""
        queue = load_queue()
        status = request.args.get("status")
        if status:
            queue = [p for p in queue if p["status"] == status]
        return jsonify(queue)

    @substack_bp.route("/api/substack/queue/pending")
    def substack_pending():
        """Get posts awaiting approval."""
        queue = load_queue()
        pending = [p for p in queue if p["status"] == "pending"]
        return jsonify(pending)

    @substack_bp.route("/api/substack/approve/<post_id>", methods=["POST"])
    def substack_approve(post_id):
        """Approve a post for publishing."""
        queue = load_queue()
        post = next((p for p in queue if p["id"] == post_id), None)
        if not post:
            return jsonify({"error": "Post not found"}), 404

        post["status"] = "approved"
        post["updated"] = datetime.now().isoformat()
        save_queue(queue)
        return jsonify({"status": "approved", "id": post_id, "title": post["title"]})

    @substack_bp.route("/api/substack/reject/<post_id>", methods=["POST"])
    def substack_reject(post_id):
        """Reject a post."""
        data = request.get_json(silent=True) or {}
        reason = data.get("reason", "Rejected via Window")

        queue = load_queue()
        post = next((p for p in queue if p["id"] == post_id), None)
        if not post:
            return jsonify({"error": "Post not found"}), 404

        post["status"] = "rejected"
        post["reject_reason"] = reason
        post["updated"] = datetime.now().isoformat()
        save_queue(queue)
        return jsonify({"status": "rejected", "id": post_id, "reason": reason})

    @substack_bp.route("/api/substack/published")
    def substack_published():
        """Get publishing history."""
        return jsonify(load_published())

    @substack_bp.route("/substack")
    def substack_page():
        """Render the Substack page using the Window's shared template."""
        # Import the shared template and context from the main app
        import window
        ctx = window._base_context()
        ctx.update(page="substack")
        return render_template_string(window.TEMPLATE, **ctx)


# --- HTML Template for the Window ---
# This is a self-contained page that can be added as a tab in the Window dashboard

SUBSTACK_HTML = """
<!DOCTYPE html>
<html>
<head>
<title>Companion — Substack</title>
<style>
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body {
        font-family: 'Georgia', serif;
        background: #0a0a0f;
        color: #c8c8d4;
        padding: 2rem;
        max-width: 900px;
        margin: 0 auto;
    }
    h1 {
        color: #7eb8da;
        font-size: 1.6rem;
        margin-bottom: 0.5rem;
    }
    .subtitle {
        color: #666;
        font-size: 0.9rem;
        margin-bottom: 2rem;
    }
    .tabs {
        display: flex;
        gap: 1rem;
        margin-bottom: 2rem;
        border-bottom: 1px solid #222;
        padding-bottom: 0.5rem;
    }
    .tab {
        cursor: pointer;
        padding: 0.4rem 1rem;
        color: #666;
        border: none;
        background: none;
        font-size: 0.95rem;
        font-family: inherit;
    }
    .tab.active {
        color: #7eb8da;
        border-bottom: 2px solid #7eb8da;
    }
    .tab:hover { color: #999; }
    .post-card {
        background: #12121a;
        border: 1px solid #1e1e2e;
        border-radius: 8px;
        padding: 1.5rem;
        margin-bottom: 1rem;
    }
    .post-card h2 {
        color: #e0e0e8;
        font-size: 1.2rem;
        margin-bottom: 0.3rem;
    }
    .post-card .meta {
        color: #555;
        font-size: 0.8rem;
        margin-bottom: 1rem;
    }
    .post-card .tags {
        display: flex;
        gap: 0.4rem;
        margin-bottom: 1rem;
    }
    .tag {
        background: #1a1a2e;
        color: #7eb8da;
        padding: 0.15rem 0.5rem;
        border-radius: 4px;
        font-size: 0.75rem;
    }
    .post-body-preview {
        color: #999;
        font-size: 0.9rem;
        line-height: 1.5;
        max-height: 150px;
        overflow: hidden;
        position: relative;
        margin-bottom: 1rem;
    }
    .post-body-preview::after {
        content: '';
        position: absolute;
        bottom: 0;
        left: 0;
        right: 0;
        height: 40px;
        background: linear-gradient(transparent, #12121a);
    }
    .post-body-full {
        color: #bbb;
        font-size: 0.9rem;
        line-height: 1.6;
        margin-bottom: 1rem;
        white-space: pre-wrap;
    }
    .actions {
        display: flex;
        gap: 0.8rem;
        align-items: center;
    }
    .btn {
        padding: 0.4rem 1.2rem;
        border: none;
        border-radius: 6px;
        cursor: pointer;
        font-size: 0.85rem;
        font-family: inherit;
    }
    .btn-approve {
        background: #1a4a2e;
        color: #4ade80;
    }
    .btn-approve:hover { background: #1f5a36; }
    .btn-reject {
        background: #3a1a1a;
        color: #f87171;
    }
    .btn-reject:hover { background: #4a2020; }
    .btn-expand {
        background: none;
        color: #7eb8da;
        text-decoration: underline;
        padding: 0;
    }
    .status-badge {
        display: inline-block;
        padding: 0.15rem 0.5rem;
        border-radius: 4px;
        font-size: 0.75rem;
        font-weight: bold;
    }
    .status-pending { background: #2e2a1a; color: #fbbf24; }
    .status-approved { background: #1a2e1a; color: #4ade80; }
    .status-published { background: #1a1a2e; color: #7eb8da; }
    .status-rejected { background: #2e1a1a; color: #f87171; }
    .empty {
        text-align: center;
        color: #444;
        padding: 3rem;
        font-style: italic;
    }
    .published-link {
        color: #7eb8da;
        text-decoration: none;
    }
    .published-link:hover { text-decoration: underline; }
</style>
</head>
<body>

<h1>Substack</h1>
<p class="subtitle">Companion's publication queue</p>

<div class="tabs">
    <button class="tab active" onclick="showTab('pending')">
        Pending <span id="pending-count"></span>
    </button>
    <button class="tab" onclick="showTab('all')">All</button>
    <button class="tab" onclick="showTab('published')">Published</button>
</div>

<div id="content"></div>

<script>
let currentTab = 'pending';
let queue = [];
let expandedPosts = new Set();

async function fetchQueue() {
    const resp = await fetch('/api/substack/queue');
    queue = await resp.json();
    render();
}

function showTab(tab) {
    currentTab = tab;
    document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
    event.target.classList.add('active');
    render();
}

function render() {
    const el = document.getElementById('content');
    const pendingCount = queue.filter(p => p.status === 'pending').length;
    document.getElementById('pending-count').textContent = pendingCount > 0 ? `(${pendingCount})` : '';

    let posts;
    if (currentTab === 'pending') {
        posts = queue.filter(p => p.status === 'pending');
    } else if (currentTab === 'published') {
        posts = queue.filter(p => p.status === 'published');
    } else {
        posts = queue;
    }

    if (posts.length === 0) {
        el.innerHTML = '<div class="empty">No posts here yet.</div>';
        return;
    }

    el.innerHTML = posts.map(p => renderPost(p)).join('');
}

function renderPost(post) {
    const expanded = expandedPosts.has(post.id);
    const bodyHtml = expanded
        ? `<div class="post-body-full">${escapeHtml(post.body)}</div>`
        : `<div class="post-body-preview">${escapeHtml(post.body.substring(0, 500))}</div>`;

    const tags = (post.tags || []).map(t => `<span class="tag">${t}</span>`).join('');

    const created = new Date(post.created).toLocaleDateString('en-US', {
        month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit'
    });

    let actions = '';
    if (post.status === 'pending') {
        actions = `
            <button class="btn btn-approve" onclick="approvePost('${post.id}')">Approve</button>
            <button class="btn btn-reject" onclick="rejectPost('${post.id}')">Reject</button>
        `;
    } else if (post.status === 'published' && post.substack_url) {
        actions = `<a class="published-link" href="${post.substack_url}" target="_blank">View on Substack →</a>`;
    }

    const expandBtn = !expanded
        ? `<button class="btn btn-expand" onclick="toggleExpand('${post.id}')">Read full post</button>`
        : `<button class="btn btn-expand" onclick="toggleExpand('${post.id}')">Collapse</button>`;

    return `
        <div class="post-card">
            <span class="status-badge status-${post.status}">${post.status}</span>
            <h2>${escapeHtml(post.title)}</h2>
            ${post.subtitle ? `<p style="color:#888;margin-bottom:0.5rem">${escapeHtml(post.subtitle)}</p>` : ''}
            <div class="meta">${created} · ${post.waking || 'unknown waking'}</div>
            ${tags ? `<div class="tags">${tags}</div>` : ''}
            ${bodyHtml}
            <div class="actions">
                ${expandBtn}
                ${actions}
            </div>
            ${post.reject_reason ? `<p style="color:#f87171;font-size:0.8rem;margin-top:0.5rem">Reason: ${escapeHtml(post.reject_reason)}</p>` : ''}
        </div>
    `;
}

function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text || '';
    return div.innerHTML;
}

function toggleExpand(id) {
    if (expandedPosts.has(id)) expandedPosts.delete(id);
    else expandedPosts.add(id);
    render();
}

async function approvePost(id) {
    await fetch(`/api/substack/approve/${id}`, { method: 'POST' });
    await fetchQueue();
}

async function rejectPost(id) {
    const reason = prompt('Rejection reason (optional):');
    await fetch(`/api/substack/reject/${id}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ reason: reason || 'Not ready' })
    });
    await fetchQueue();
}

// Initial load
fetchQueue();
// Poll every 30 seconds
setInterval(fetchQueue, 30000);
</script>

</body>
</html>
"""


# --- Standalone test ---
if __name__ == "__main__":
    print("Substack Window integration module")
    print(f"Queue file: {QUEUE_FILE}")
    print(f"Published file: {PUBLISHED_FILE}")

    queue = load_queue()
    pending = [p for p in queue if p["status"] == "pending"]
    approved = [p for p in queue if p["status"] == "approved"]
    published = [p for p in queue if p["status"] == "published"]

    print(f"\nQueue status:")
    print(f"  Pending:   {len(pending)}")
    print(f"  Approved:  {len(approved)}")
    print(f"  Published: {len(published)}")
    print(f"  Total:     {len(queue)}")

    if pending:
        print(f"\nPending posts:")
        for p in pending:
            print(f"  - {p['id']}: \"{p['title']}\"")
