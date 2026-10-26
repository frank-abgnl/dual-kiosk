"""
CSS Styling for XInput Pseudo-Seating GTK Application.
Compatible with standard GTK 3 CSS engine.
"""

CSS_STYLES = b"""
/* Card styling */
.card {
    border: 2px solid #3584e4;
    border-radius: 8px;
    padding: 12px;
    margin: 6px;
}
.card-good  { border-color: #26a269; }
.card-warn  { border-color: #e5a50a; }
.card-error { border-color: #c01c28; }
.card-value {
    font-size: 26px;
    font-weight: bold;
}
.card-label {
    font-size: 11px;
    font-weight: bold;
    color: #5e5c64;
}
.section-title {
    font-weight: bold;
    font-size: 14px;
    margin-bottom: 6px;
}

/* Action button styles */
.btn-primary {
    background-image: none;
    background-color: #3584e4;
    color: white;
    font-weight: bold;
    border-radius: 6px;
    padding: 6px 14px;
}
.btn-success {
    background-image: none;
    background-color: #26a269;
    color: white;
    font-weight: bold;
    border-radius: 6px;
    padding: 6px 14px;
}
.btn-danger {
    background-image: none;
    background-color: #c01c28;
    color: white;
    font-weight: bold;
    border-radius: 6px;
    padding: 6px 14px;
}

/* Seat container card */
.seat-card {
    border: 1px solid #c0bfbc;
    border-radius: 8px;
    padding: 14px;
    margin: 6px;
}

/* Status Badges */
.badge-active {
    background-color: #26a269;
    color: white;
    font-weight: bold;
    font-size: 11px;
    border-radius: 10px;
    padding: 2px 8px;
}
.badge-inactive {
    background-color: #77767b;
    color: white;
    font-weight: bold;
    font-size: 11px;
    border-radius: 10px;
    padding: 2px 8px;
}

/* Log view */
.log-view {
    font-family: monospace;
    font-size: 11px;
    background-color: #1e1e1e;
    color: #f6f5f4;
    padding: 8px;
}
"""
