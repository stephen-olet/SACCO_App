import streamlit as st
import pandas as pd
import sqlite3
import os, secrets, hashlib, hmac, base64, re
from datetime import date, datetime
from typing import Tuple, List, Dict

"""
SACCO App

This Streamlit application provides a full-featured SACCO management system
with authentication, roles (admin vs teller), interest calculations,
brandable settings, backup/restore, and Paystore-ready placeholders.

To run this app locally:
    streamlit run sacco_app.py

The app is designed to be hosted at an HTTPS URL and wrapped into
an Android WebView or Trusted Web Activity for deployment on Google Play.
"""

# Configure the Streamlit page (title, icon, layout)
st.set_page_config(page_title="SACCO", page_icon="💳", layout="wide")

# Primary button styling parameters for consistency
PRIMARY_BTN_KW = {"use_container_width": True, "type": "primary"}

# -----------------------------------------------------------------------------
# Security helpers (password hashing)
# -----------------------------------------------------------------------------
def _pbkdf2(password: str, salt_b64: str = None, iterations: int = 130_000):
    """Return PBKDF2-HMAC-SHA256 derived key and salt (in base64)."""
    salt = base64.b64decode(salt_b64) if salt_b64 else os.urandom(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return iterations, base64.b64encode(salt).decode(), base64.b64encode(dk).decode()

def hash_password(password: str) -> str:
    """Generate a salted PBKDF2 hash for the given password."""
    it, salt_b64, dk_b64 = _pbkdf2(password)
    return f"pbkdf2_sha256${it}${salt_b64}${dk_b64}"

def verify_password(password: str, stored: str) -> bool:
    """Verify a password against the stored PBKDF2 hash."""
    try:
        algo, it_s, salt_b64, dk_b64 = stored.split("$")
        assert algo == "pbkdf2_sha256"
        it = int(it_s)
    except Exception:
        return False
    _, _, new_dk = _pbkdf2(password, salt_b64=salt_b64, iterations=it)
    return hmac.compare_digest(new_dk, dk_b64)

def money(amount: float, currency: str) -> str:
    """Return a formatted currency string."""
    return f"{currency} {amount:,.2f}"

# -----------------------------------------------------------------------------
# Database initialization and helpers
# -----------------------------------------------------------------------------
@st.cache_resource
def get_conn() -> sqlite3.Connection:
    """Create and return a SQLite connection with foreign key support."""
    conn = sqlite3.connect("sacco.db", check_same_thread=False)
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn

def init_db(conn: sqlite3.Connection) -> None:
    """Initialize all tables and seed default settings and admin user."""
    c = conn.cursor()
    # Users table
    c.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            role TEXT NOT NULL CHECK(role IN ('admin','teller')),
            created_at TEXT NOT NULL
        );
        """
    )
    # Members table
    c.execute(
        """
        CREATE TABLE IF NOT EXISTS members (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            member_id TEXT NOT NULL UNIQUE,
            member_name TEXT NOT NULL,
            member_contact TEXT,
            email_address TEXT,
            registration_date TEXT NOT NULL
        );
        """
    )
    # Savings/Deposits table
    c.execute(
        """
        CREATE TABLE IF NOT EXISTS savings_deposits (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            amount REAL NOT NULL CHECK(amount >= 0),
            date TEXT NOT NULL,
            transaction_id TEXT NOT NULL UNIQUE,
            member_id TEXT NOT NULL,
            interest_rate REAL DEFAULT 0,
            FOREIGN KEY(member_id) REFERENCES members(member_id) ON DELETE CASCADE
        );
        """
    )
    # Loans table
    c.execute(
        """
        CREATE TABLE IF NOT EXISTS loans (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            loan_amount REAL NOT NULL CHECK(loan_amount >= 0),
            loan_period INTEGER NOT NULL CHECK(loan_period >= 1),
            total_repayment REAL NOT NULL,
            monthly_installment REAL NOT NULL,
            loan_date TEXT NOT NULL,
            loan_transaction_id TEXT NOT NULL UNIQUE,
            member_id TEXT NOT NULL,
            interest_rate REAL NOT NULL DEFAULT 0,
            FOREIGN KEY(member_id) REFERENCES members(member_id) ON DELETE CASCADE
        );
        """
    )
    # Organization settings table
    c.execute(
        """
        CREATE TABLE IF NOT EXISTS org_settings (
            id INTEGER PRIMARY KEY CHECK (id=1),
            org_name TEXT NOT NULL DEFAULT 'Your SACCO',
            currency TEXT NOT NULL DEFAULT 'UGX',
            primary_color TEXT NOT NULL DEFAULT '#0f766e',
            default_savings_rate REAL NOT NULL DEFAULT 10.0,
            default_compounding TEXT NOT NULL DEFAULT 'Daily' -- 'Daily' or 'Monthly'
        );
        """
    )
    # Payments table (for Paystore placeholders)
    c.execute(
        """
        CREATE TABLE IF NOT EXISTS payments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            payment_type TEXT NOT NULL CHECK(payment_type IN ('deposit','loan_repayment')),
            member_id TEXT NOT NULL,
            amount REAL NOT NULL CHECK(amount > 0),
            currency TEXT NOT NULL DEFAULT 'UGX',
            external_ref TEXT,
            status TEXT NOT NULL CHECK(status IN ('PENDING','SUCCESS','FAILED')) DEFAULT 'PENDING',
            created_at TEXT NOT NULL,
            meta_json TEXT,
            FOREIGN KEY(member_id) REFERENCES members(member_id) ON DELETE CASCADE
        );
        """
    )
    conn.commit()
    # Seed settings row if absent
    if not conn.execute("SELECT 1 FROM org_settings WHERE id=1").fetchone():
        conn.execute("INSERT INTO org_settings (id) VALUES (1)")
        conn.commit()
    # Seed default admin user if no users exist
    if conn.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0:
        conn.execute(
            "INSERT INTO users (username, password_hash, role, created_at) VALUES (?, ?, 'admin', ?)",
            ("admin", hash_password("change_me_now!"), datetime.utcnow().isoformat())
        )
        conn.commit()

# Establish connection and initialize DB
conn = get_conn()
init_db(conn)

# -----------------------------------------------------------------------------
# Settings cache
# -----------------------------------------------------------------------------
@st.cache_data
def get_settings() -> Dict[str, str]:
    """Load organization settings into a dictionary."""
    row = conn.execute(
        "SELECT org_name, currency, primary_color, default_savings_rate, default_compounding FROM org_settings WHERE id=1"
    ).fetchone()
    return dict(
        org_name=row[0],
        currency=row[1],
        primary_color=row[2],
        default_savings_rate=row[3],
        default_compounding=row[4],
    )

def refresh_settings_cache() -> None:
    """Clear cached settings so next call reloads fresh data."""
    get_settings.clear()

# -----------------------------------------------------------------------------
# Members cache
# -----------------------------------------------------------------------------
@st.cache_data
def load_members_df() -> pd.DataFrame:
    """Fetch and cache the members DataFrame sorted by name."""
    return pd.read_sql_query(
        "SELECT id, member_id, member_name, member_contact, email_address, registration_date FROM members ORDER BY member_name COLLATE NOCASE ASC",
        conn
    )

def refresh_members_cache() -> None:
    """Clear cached members data."""
    load_members_df.clear()

def member_choice_map(df: pd.DataFrame) -> Tuple[List[str], Dict[str, str]]:
    """Create a display list and mapping for member SelectBoxes."""
    if df.empty:
        return [], {}
    labels = [f"{row.member_name} (ID: {row.member_id})" for row in df.itertuples(index=False)]
    mapping = {labels[i]: df.iloc[i]["member_id"] for i in range(len(df))}
    return labels, mapping

# -----------------------------------------------------------------------------
# Authentication and session management
# -----------------------------------------------------------------------------
def login_box() -> None:
    """Render a login form and update session state upon successful login."""
    with st.form("login_form"):
        st.markdown("### Sign in")
        username = st.text_input("Username").strip()
        password = st.text_input("Password", type="password")
        submitted = st.form_submit_button("Login", **PRIMARY_BTN_KW)
    if submitted:
        row = conn.execute(
            "SELECT username, password_hash, role FROM users WHERE username = ?",
            (username,)
        ).fetchone()
        if row and verify_password(password, row[1]):
            st.session_state["user"] = {"username": row[0], "role": row[2]}
            st.success(f"Welcome, {row[0]} ({row[2]})")
            st.experimental_rerun()
        else:
            st.error("Invalid username or password")

def require_auth() -> None:
    """Ensure a user is logged in; otherwise render login box and stop app."""
    if "user" not in st.session_state:
        draw_header()
        login_box()
        draw_footer()
        st.stop()

def require_role(*roles: str) -> None:
    """Ensure the logged-in user has one of the specified roles."""
    user = st.session_state.get("user")
    if not user or user.get("role") not in roles:
        st.warning("You don’t have permission for this section.")
        st.stop()

# -----------------------------------------------------------------------------
# Interest calculations
# -----------------------------------------------------------------------------
def years_between(d0: date, d1: date) -> float:
    """Return the number of years between two dates as a float."""
    return (d1 - d0).days / 365.0

def compound_amount(P: float, annual_rate_pct: float, start: date, end: date, freq: str) -> float:
    """Calculate compound interest amount with daily or monthly compounding."""
    if annual_rate_pct <= 0:
        return P
    n = 365 if freq == "Daily" else 12
    t = years_between(start, end)
    return P * ((1 + (annual_rate_pct / 100.0) / n) ** (n * t))

def amortization_schedule(principal: float, annual_rate_pct: float, months: int, start_date: date) -> pd.DataFrame:
    """Return an amortization schedule for equal payments."""
    r = (annual_rate_pct / 100.0) / 12.0
    if r == 0:
        payment = principal / months
    else:
        payment = principal * (r * (1 + r) ** months) / ((1 + r) ** months - 1)
    rows = []
    balance = principal
    current_date = start_date
    for m in range(1, months + 1):
        interest = balance * r
        principal_part = payment - interest
        balance = max(0.0, balance - principal_part)
        rows.append({
            "Installment #": m,
            "Due Date": current_date.isoformat(),
            "Payment": payment,
            "Interest": interest,
            "Principal": principal_part,
            "Balance": balance,
        })
        current_date = date.fromordinal(current_date.toordinal() + 30)
    return pd.DataFrame(rows)

# -----------------------------------------------------------------------------
# Styling helpers (header and footer)
# -----------------------------------------------------------------------------
def draw_header() -> None:
    """Render a sticky app bar with organization name and user info."""
    settings = get_settings()
    user = st.session_state.get("user", {})
    st.markdown(
        f"""
        <style>
            :root {{
                --brand: {settings['primary_color']};
            }}
            .appbar {{
                position: sticky;
                top: 0;
                z-index: 999;
                background: var(--brand);
                color: #fff;
                padding: 10px 16px;
                border-radius: 10px;
            }}
            .appbar h1 {{ font-size: 20px; margin: 0; }}
            .foot {{ margin-top: 24px; padding: 8px 12px; border-top: 1px solid #eee; color: #6b7280; }}
            .card-empty {{ border: 1px dashed #cbd5e1; padding: 16px; border-radius: 10px; background: #f8fafc; }}
        </style>
        <div class="appbar">
            <div style="display:flex; align-items:center; justify-content:space-between;">
                <h1>{settings['org_name']} · SACCO</h1>
                <div>Signed in as <strong>{user.get('username', '-')}</strong> · Role: <strong>{user.get('role', '-')}
                </strong></div>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

def draw_footer() -> None:
    """Render a small footer section."""
    st.markdown(
        f'<div class="foot">© {date.today().year} • SACCO • Built on Streamlit</div>',
        unsafe_allow_html=True,
    )

# -----------------------------------------------------------------------------
# Global Nav and page selection
# -----------------------------------------------------------------------------
def render_sidebar() -> str:
    """Render navigation radio and return selected page."""
    settings = get_settings()
    user = st.session_state["user"]
    with st.sidebar:
        st.title("Navigation")
        pages = ["Dashboard", "Member Management", "Savings & Deposits", "Loan Management", "Financial Summary"]
        if user["role"] == "admin":
            pages.append("Admin")
        selected_page = st.radio("Go to:", pages)
        if st.button("Logout", use_container_width=True):
            st.session_state.pop("user", None)
            st.experimental_rerun()
    return selected_page

# -----------------------------------------------------------------------------
# Draw header and ensure authentication
# -----------------------------------------------------------------------------
require_auth()
selected_page = render_sidebar()
settings = get_settings()
draw_header()
user = st.session_state["user"]

# -----------------------------------------------------------------------------
# Dashboard page
# -----------------------------------------------------------------------------
if selected_page == "Dashboard":
    st.subheader("Overview")
    members_df = load_members_df()
    total_members = len(members_df)
    total_savings = conn.execute("SELECT COALESCE(SUM(amount),0) FROM savings_deposits").fetchone()[0] or 0.0
    total_loans = conn.execute("SELECT COALESCE(SUM(loan_amount),0) FROM loans").fetchone()[0] or 0.0
    col1, col2, col3 = st.columns(3)
    col1.metric("Members", f"{total_members}")
    col2.metric("Total Savings", money(float(total_savings), settings["currency"]))
    col3.metric("Total Loan Principal", money(float(total_loans), settings["currency"]))
    st.markdown("—")

# -----------------------------------------------------------------------------
# Member Management page
# -----------------------------------------------------------------------------
elif selected_page == "Member Management":
    st.subheader("Add Member")
    with st.form("add_member_form", clear_on_submit=True):
        col1, col2 = st.columns(2)
        with col1:
            member_id = st.text_input("Member ID *").strip()
            member_name = st.text_input("Member Name *").strip()
            member_contact = st.text_input(
                "Contact (e.g., +2567…)", help="International format recommended"
            ).strip()
        with col2:
            email = st.text_input("Email Address", help="example@domain.com").strip()
            registration_date = st.date_input(
                "Registration Date *", value=date.today(), max_value=date.today()
            )
        submit_member = st.form_submit_button("Register Member", **PRIMARY_BTN_KW)
    if submit_member:
        if not member_id or not member_name:
            st.error("Member ID and Name are required.")
        elif email and not re.match(r"^[^@]+@[^@]+\.[^@]+$", email):
            st.error("Invalid email address.")
        else:
            try:
                conn.execute(
                    "INSERT INTO members (member_id, member_name, member_contact, email_address, registration_date) VALUES (?,?,?,?,?)",
                    (
                        member_id,
                        member_name,
                        member_contact,
                        email,
                        registration_date.isoformat(),
                    ),
                )
                conn.commit()
                refresh_members_cache()
                st.success(f"Member {member_name} (ID {member_id}) registered.")
            except sqlite3.IntegrityError as e:
                st.error(f"Could not register: {e}")
    st.subheader("Registered Members")
    members_df = load_members_df()
    if members_df.empty:
        st.markdown(
            '<div class="card-empty">No members yet. Add your first member above.</div>',
            unsafe_allow_html=True,
        )
    else:
        show = members_df.rename(
            columns={
                "id": "ID",
                "member_id": "Member ID",
                "member_name": "Name",
                "member_contact": "Contact",
                "email_address": "Email",
                "registration_date": "Registered",
            }
        )
        st.dataframe(show, use_container_width=True)
        st.download_button(
            "Download CSV",
            show.to_csv(index=False).encode(),
            "members.csv",
            "text/csv",
        )
        st.markdown("—")
        st.subheader("Delete Member")
        if user["role"] != "admin":
            st.info("Only admins can delete members.")
        else:
            labels, mapping = member_choice_map(members_df)
            pick = st.selectbox("Select member", labels) if labels else None
            if st.button("Delete Member", **PRIMARY_BTN_KW) and pick:
                conn.execute("DELETE FROM members WHERE member_id=?", (mapping[pick],))
                conn.commit()
                refresh_members_cache()
                st.success(f"Deleted {pick} (and related transactions).")

# -----------------------------------------------------------------------------
# Savings & Deposits page
# -----------------------------------------------------------------------------
elif selected_page == "Savings & Deposits":
    st.subheader("Record Deposit")
    members_df = load_members_df()
    if members_df.empty:
        st.warning("Add members first.")
    else:
        labels, mapping = member_choice_map(members_df)
        pick = st.selectbox("Member", labels)
        mid = mapping[pick]
        with st.form("deposit_form", clear_on_submit=True):
            c1, c2, c3 = st.columns(3)
            with c1:
                amount = st.number_input(
                    "Amount (UGX) *", min_value=0.0, step=1_000.0
                )
            with c2:
                deposit_date = st.date_input(
                    "Date *", value=date.today(), max_value=date.today()
                )
            with c3:
                deposit_rate = st.number_input(
                    "Interest rate (%)",
                    min_value=0.0,
                    value=float(settings["default_savings_rate"]),
                    step=0.1,
                )
            transaction_id = st.text_input("Transaction ID *").strip()
            submit_deposit = st.form_submit_button("Record Deposit", **PRIMARY_BTN_KW)
        if submit_deposit:
            if amount <= 0 or not transaction_id:
                st.error(
                    "Amount must be > 0 and Transaction ID is required."
                )
            else:
                try:
                    conn.execute(
                        "INSERT INTO savings_deposits (amount, date, transaction_id, member_id, interest_rate) VALUES (?,?,?,?,?)",
                        (
                            float(amount),
                            deposit_date.isoformat(),
                            transaction_id,
                            mid,
                            float(deposit_rate),
                        ),
                    )
                    conn.commit()
                    st.success(
                        f"Saved {money(amount, settings['currency'])} for {pick}."
                    )
                except sqlite3.IntegrityError as e:
                    st.error(f"Save failed: {e}")
        # Paystore deposit
        st.markdown("**Pay with Paystore (placeholder)**")
        with st.form("paystore_deposit_form"):
            pay_amount = st.number_input(
                "Deposit amount", min_value=0.0, step=1_000.0, key="ps_dep_amount"
            )
            submit_pay = st.form_submit_button(
                "Pay with Paystore", **PRIMARY_BTN_KW
            )
        if submit_pay:
            if pay_amount <= 0:
                st.error("Enter a positive amount.")
            else:
                ref = "PS-" + secrets.token_hex(6).upper()
                conn.execute(
                    "INSERT INTO payments (payment_type, member_id, amount, currency, external_ref, status, created_at, meta_json) VALUES ('deposit',?,?,?,?, 'PENDING', ?, ?)",
                    (
                        mid,
                        float(pay_amount),
                        settings["currency"],
                        ref,
                        datetime.utcnow().isoformat(),
                        '{"note":"Integrate Paystore API here"}',
                    ),
                )
                conn.commit()
                st.success(
                    f"PENDING: {ref} for {money(pay_amount, settings['currency'])} (hook to Paystore API)."
                )
        # Savings history
        st.subheader("Savings History")
        df_savings = pd.read_sql_query(
            """
            SELECT s.amount, s.date, s.transaction_id, s.member_id, s.interest_rate, m.member_name
            FROM savings_deposits s JOIN members m ON s.member_id = m.member_id
            WHERE s.member_id = ? ORDER BY s.date DESC, s.id DESC
            """,
            conn,
            params=(mid,),
        )
        if df_savings.empty:
            st.markdown(
                '<div class="card-empty">No savings yet for this member.</div>',
                unsafe_allow_html=True,
            )
        else:
            view = df_savings.rename(
                columns={
                    "amount": "Amount",
                    "member_name": "Member",
                    "date": "Date",
                    "transaction_id": "Transaction",
                    "member_id": "Member ID",
                    "interest_rate": "Rate (%)",
                }
            )
            view.index = view.index + 1
            st.dataframe(
                view[
                    [
                        "Amount",
                        "Member",
                        "Date",
                        "Transaction",
                        "Member ID",
                        "Rate (%)",
                    ]
                ],
                use_container_width=True,
            )
            st.metric(
                "TOTAL SAVINGS",
                money(float(view["Amount"].sum()), settings["currency"]),
            )
            st.download_button(
                "Download CSV",
                view.to_csv(index=False).encode(),
                "savings.csv",
                "text/csv",
            )
        # Interest calculator
        st.markdown("—")
        st.subheader("Interest Calculator (Per-Deposit)")
        c1, c2, c3 = st.columns(3)
        with c1:
            annual_rate = st.number_input(
                "Annual rate (%)", min_value=0.0, value=float(settings["default_savings_rate"]), step=0.1
            )
        with c2:
            compounding = st.selectbox(
                "Compounding", ["Daily", "Monthly"], index=(0 if settings["default_compounding"] == "Daily" else 1)
            )
        with c3:
            as_of = st.date_input(
                "As of", value=date.today(), max_value=date.today()
            )
        if not df_savings.empty:
            total_principal = 0.0
            total_interest = 0.0
            for record in df_savings.itertuples(index=False):
                principal = float(record.amount)
                start = date.fromisoformat(record.date)
                amount_after = compound_amount(principal, annual_rate, start, as_of, compounding)
                total_principal += principal
                total_interest += (amount_after - principal)
            st.markdown(f"**Principal considered:** {money(total_principal, settings['currency'])}")
            st.markdown(
                f"**Accrued interest ({compounding.lower()} to {as_of.isoformat()}):** {money(total_interest, settings['currency'])}"
            )
            if user["role"] == "admin" and st.button(
                "Post Interest as Deposit", **PRIMARY_BTN_KW
            ):
                txn_ref = f"INT-{as_of.strftime('%Y%m%d')}-{secrets.token_hex(3)}"
                conn.execute(
                    "INSERT INTO savings_deposits (amount, date, transaction_id, member_id, interest_rate) VALUES (?,?,?,?,?)",
                    (
                        float(total_interest),
                        as_of.isoformat(),
                        txn_ref,
                        mid,
                        float(annual_rate),
                    ),
                )
                conn.commit()
                st.success(
                    f"Interest posted: {txn_ref} · {money(total_interest, settings['currency'])}"
                )
        # Delete savings
        st.markdown("—")
        st.subheader("Delete Savings Transaction")
        if user["role"] != "admin":
            st.info("Only admins can delete transactions.")
        else:
            delete_txn = st.text_input("Transaction ID").strip()
            if st.button("DELETE Savings", **PRIMARY_BTN_KW) and delete_txn:
                cur = conn.execute(
                    "DELETE FROM savings_deposits WHERE transaction_id = ?",
                    (delete_txn,),
                )
                conn.commit()
                if cur.rowcount:
                    st.success("Deleted.")
                else:
                    st.warning("Not found.")

# -----------------------------------------------------------------------------
# Loan Management page
# -----------------------------------------------------------------------------
elif selected_page == "Loan Management":
    st.subheader("Record Loan")
    members_df = load_members_df()
    if members_df.empty:
        st.warning("Add members first.")
    else:
        labels, mapping = member_choice_map(members_df)
        pick = st.selectbox("Member", labels)
        mid = mapping[pick]
        with st.form("loan_form", clear_on_submit=True):
            c1, c2, c3 = st.columns(3)
            with c1:
                loan_amount = st.number_input(
                    "Amount (UGX) *", min_value=0.0, step=10_000.0
                )
            with c2:
                loan_period = st.number_input(
                    "Period (months) *", min_value=1, max_value=120, value=12, step=1
                )
            with c3:
                loan_rate = st.number_input(
                    "Annual interest (%) *", min_value=0.0, value=12.0, step=0.1
                )
            c4, c5 = st.columns(2)
            with c4:
                loan_date = st.date_input(
                    "Loan date *", value=date.today(), max_value=date.today()
                )
            with c5:
                loan_txn_id = st.text_input("Loan Txn ID *").strip()
            submit_loan = st.form_submit_button("Record Loan", **PRIMARY_BTN_KW)
        if submit_loan:
            if loan_amount <= 0 or not loan_txn_id:
                st.error("Amount must be > 0 and Txn ID is required.")
            else:
                total = float(loan_amount) * (1.0 + float(loan_rate) / 100.0)
                monthly = total / int(loan_period)
                try:
                    conn.execute(
                        """
                        INSERT INTO loans (loan_amount, loan_period, total_repayment, monthly_installment, loan_date, loan_transaction_id, member_id, interest_rate)
                        VALUES (?,?,?,?,?,?,?,?)
                        """,
                        (
                            float(loan_amount),
                            int(loan_period),
                            total,
                            monthly,
                            loan_date.isoformat(),
                            loan_txn_id,
                            mid,
                            float(loan_rate),
                        ),
                    )
                    conn.commit()
                    st.success(
                        f"Loan recorded for {pick}. Total {money(total, settings['currency'])} · Monthly {money(monthly, settings['currency'])}"
                    )
                except sqlite3.IntegrityError as e:
                    st.error(f"Save failed: {e}")
        # Paystore repayment
        st.markdown("**Pay with Paystore for Loan Repayment (placeholder)**")
        with st.form("paystore_loan_form"):
            repay_amount = st.number_input(
                "Repayment amount", min_value=0.0, step=10_000.0
            )
            repay_submit = st.form_submit_button(
                "Pay with Paystore", **PRIMARY_BTN_KW
            )
        if repay_submit:
            if repay_amount <= 0:
                st.error("Enter positive amount.")
            else:
                ref = "PS-" + secrets.token_hex(6).upper()
                conn.execute(
                    "INSERT INTO payments (payment_type, member_id, amount, currency, external_ref, status, created_at, meta_json) VALUES ('loan_repayment',?,?,?,?, 'PENDING', ?, ?)",
                    (
                        mid,
                        float(repay_amount),
                        settings["currency"],
                        ref,
                        datetime.utcnow().isoformat(),
                        '{"note":"Integrate Paystore API here"}',
                    ),
                )
                conn.commit()
                st.success(
                    f"PENDING: {ref} for {money(repay_amount, settings['currency'])}"
                )
        # Loan history
        st.subheader("Loan History + Amortization")
        df_loans = pd.read_sql_query(
            """
            SELECT l.loan_amount, l.loan_period, l.total_repayment, l.monthly_installment, l.loan_date, l.loan_transaction_id,
                   l.member_id, l.interest_rate, m.member_name
            FROM loans l JOIN members m ON l.member_id = m.member_id
            WHERE l.member_id = ? ORDER BY l.loan_date DESC, l.id DESC
            """,
            conn,
            params=(mid,),
        )
        if df_loans.empty:
            st.markdown(
                '<div class="card-empty">No loans yet for this member.</div>',
                unsafe_allow_html=True,
            )
        else:
            view = df_loans.rename(
                columns={
                    "loan_amount": "Loan Amount",
                    "loan_period": "Loan Period (mo)",
                    "total_repayment": "Total Repayment",
                    "monthly_installment": "Monthly Installment",
                    "loan_date": "Loan Date",
                    "loan_transaction_id": "Transaction",
                    "member_id": "Member ID",
                    "interest_rate": "Rate (%)",
                    "member_name": "Member",
                }
            )
            view.index = view.index + 1
            st.dataframe(
                view[
                    [
                        "Loan Amount",
                        "Member",
                        "Loan Period (mo)",
                        "Rate (%)",
                        "Total Repayment",
                        "Monthly Installment",
                        "Loan Date",
                        "Transaction",
                        "Member ID",
                    ]
                ],
                use_container_width=True,
            )
            selected_txn = st.selectbox(
                "Choose loan",
                list(view["Transaction"]),
            )
            selected_row = view[view["Transaction"] == selected_txn].iloc[0]
            schedule = amortization_schedule(
                float(selected_row["Loan Amount"]),
                float(selected_row["Rate (%)"]),
                int(selected_row["Loan Period (mo)"]),
                date.fromisoformat(selected_row["Loan Date"]),
            )
            st.dataframe(
                schedule.style.format(
                    {
                        "Payment": "{:,.2f}",
                        "Interest": "{:,.2f}",
                        "Principal": "{:,.2f}",
                        "Balance": "{:,.2f}",
                    }
                ),
                use_container_width=True,
            )
            as_of_date = st.date_input(
                "Outstanding as of", value=date.today(), max_value=date.today()
            )
            due_mask = pd.to_datetime(schedule["Due Date"]).dt.date <= as_of_date
            if due_mask.any():
                last_row = schedule[due_mask].iloc[-1]
                outstanding_balance = float(last_row["Balance"])
            else:
                outstanding_balance = float(selected_row["Loan Amount"])
            st.metric(
                "Outstanding Balance",
                money(outstanding_balance, settings["currency"]),
            )
        # Delete loans
        st.markdown("—")
        st.subheader("Delete Loan Transaction")
        if user["role"] != "admin":
            st.info("Only admins can delete transactions.")
        else:
            del_txn = st.text_input("Loan Txn ID").strip()
            if st.button("DELETE Loan", **PRIMARY_BTN_KW) and del_txn:
                cur = conn.execute(
                    "DELETE FROM loans WHERE loan_transaction_id = ?",
                    (del_txn,),
                )
                conn.commit()
                if cur.rowcount:
                    st.success("Deleted.")
                else:
                    st.warning("Not found.")

# -----------------------------------------------------------------------------
# Financial Summary page
# -----------------------------------------------------------------------------
elif selected_page == "Financial Summary":
    st.subheader("Savings & Deposits")
    members_df = load_members_df()
    labels, mapping = member_choice_map(members_df)
    scope = st.selectbox(
        "Scope",
        ["All Members"] + labels,
    )
    if scope == "All Members":
        df_savings = pd.read_sql_query(
            """
            SELECT s.amount, s.date, s.transaction_id, s.member_id, s.interest_rate, m.member_name
            FROM savings_deposits s JOIN members m ON s.member_id = m.member_id
            ORDER BY s.date DESC, s.id DESC
            """,
            conn,
        )
        df_loans = pd.read_sql_query(
            """
            SELECT l.loan_amount, l.loan_period, l.total_repayment, l.monthly_installment, l.loan_date, l.loan_transaction_id,
                   l.member_id, l.interest_rate, m.member_name
            FROM loans l JOIN members m ON l.member_id = m.member_id
            ORDER BY l.loan_date DESC, l.id DESC
            """,
            conn,
        )
    else:
        mid = mapping[scope]
        df_savings = pd.read_sql_query(
            """
            SELECT s.amount, s.date, s.transaction_id, s.member_id, s.interest_rate, m.member_name
            FROM savings_deposits s JOIN members m ON s.member_id = m.member_id
            WHERE s.member_id = ? ORDER BY s.date DESC, s.id DESC
            """,
            conn,
            params=(mid,),
        )
        df_loans = pd.read_sql_query(
            """
            SELECT l.loan_amount, l.loan_period, l.total_repayment, l.monthly_installment, l.loan_date, l.loan_transaction_id,
                   l.member_id, l.interest_rate, m.member_name
            FROM loans l JOIN members m ON l.member_id = m.member_id
            WHERE l.member_id = ? ORDER BY l.loan_date DESC, l.id DESC
            """,
            conn,
            params=(mid,),
        )
    # Summaries
    if df_savings.empty:
        st.write("No savings.")
    else:
        sv = df_savings.rename(
            columns={
                "amount": "Amount",
                "member_name": "Member",
                "date": "Date",
                "transaction_id": "Transaction",
                "member_id": "Member ID",
                "interest_rate": "Rate (%)",
            }
        )
        sv.index = sv.index + 1
        st.dataframe(
            sv[[
                "Amount",
                "Member",
                "Date",
                "Transaction",
                "Member ID",
                "Rate (%)",
            ]],
            use_container_width=True,
        )
        st.markdown(
            f"**TOTAL SAVINGS: {money(float(sv['Amount'].sum()), settings['currency'])}**"
        )
    st.markdown("—")
    st.subheader("Loans")
    if df_loans.empty:
        st.write("No loans.")
    else:
        ln = df_loans.rename(
            columns={
                "loan_amount": "Loan Amount",
                "member_name": "Member",
                "loan_period": "Loan Period (mo)",
                "interest_rate": "Rate (%)",
                "total_repayment": "Total Repayment",
                "monthly_installment": "Monthly Installment",
                "loan_date": "Loan Date",
                "loan_transaction_id": "Transaction",
                "member_id": "Member ID",
            }
        )
        ln.index = ln.index + 1
        st.dataframe(
            ln[[
                "Loan Amount",
                "Member",
                "Loan Period (mo)",
                "Rate (%)",
                "Total Repayment",
                "Monthly Installment",
                "Loan Date",
                "Transaction",
                "Member ID",
            ]],
            use_container_width=True,
        )
        st.markdown(
            f"**TOTAL LOAN PRINCIPAL: {money(float(ln['Loan Amount'].sum()), settings['currency'])}**"
        )

# -----------------------------------------------------------------------------
# Admin page
# -----------------------------------------------------------------------------
elif selected_page == "Admin":
    require_role("admin")
    st.subheader("Organization Settings")
    current_settings = get_settings()
    with st.form("org_settings_form", clear_on_submit=False):
        org_name = st.text_input("Organization Name *", value=current_settings["org_name"])
        currency_code = st.text_input(
            "Currency code *", value=current_settings["currency"]
        )
        primary_color = st.color_picker(
            "Primary color", value=current_settings["primary_color"]
        )
        default_rate = st.number_input(
            "Default savings rate (%)",
            min_value=0.0,
            value=float(current_settings["default_savings_rate"]),
            step=0.1,
        )
        default_compounding = st.selectbox(
            "Default compounding",
            ["Daily", "Monthly"],
            index=(0 if current_settings["default_compounding"] == "Daily" else 1),
        )
        save_settings = st.form_submit_button("Save Settings", **PRIMARY_BTN_KW)
    if save_settings:
        conn.execute(
            "UPDATE org_settings SET org_name=?, currency=?, primary_color=?, default_savings_rate=?, default_compounding=? WHERE id=1",
            (
                org_name.strip() or "Your SACCO",
                currency_code.strip() or "UGX",
                primary_color,
                float(default_rate),
                default_compounding,
            ),
        )
        conn.commit()
        refresh_settings_cache()
        st.success(
            "Settings saved. You may refresh the page to see color changes everywhere."
        )
    # User management
    st.markdown("—")
    st.subheader("User Management")
    users_df = pd.read_sql_query(
        "SELECT id, username, role, created_at FROM users ORDER BY username ASC",
        conn,
    )
    st.dataframe(
        users_df.rename(
            columns={
                "id": "ID",
                "username": "Username",
                "role": "Role",
                "created_at": "Created",
            }
        ),
        use_container_width=True,
    )
    with st.form("add_user_form", clear_on_submit=True):
        new_username = st.text_input("Username *").strip()
        new_password = st.text_input("Password *", type="password")
        new_password_confirm = st.text_input("Confirm Password *", type="password")
        new_role = st.selectbox("Role *", ["admin", "teller"])
        add_user_submit = st.form_submit_button("Create User", **PRIMARY_BTN_KW)
    if add_user_submit:
        if not new_username or not new_password or new_password != new_password_confirm:
            st.error("Please fill all fields and ensure passwords match.")
        else:
            try:
                conn.execute(
                    "INSERT INTO users (username, password_hash, role, created_at) VALUES (?,?,?,?)",
                    (
                        new_username,
                        hash_password(new_password),
                        new_role,
                        datetime.utcnow().isoformat(),
                    ),
                )
                conn.commit()
                st.success(f"User {new_username} created.")
            except sqlite3.IntegrityError as e:
                st.error(f"Could not create: {e}")
    # Change password
    with st.form("change_password_form", clear_on_submit=True):
        change_user = st.text_input("Username *").strip()
        new_pw1 = st.text_input("New Password *", type="password")
        new_pw2 = st.text_input("Confirm *", type="password")
        change_pw_submit = st.form_submit_button("Update Password", **PRIMARY_BTN_KW)
    if change_pw_submit:
        if not change_user or not new_pw1 or new_pw1 != new_pw2:
            st.error("Provide username and matching passwords.")
        else:
            cur = conn.execute(
                "UPDATE users SET password_hash=? WHERE username=?",
                (hash_password(new_pw1), change_user),
            )
            conn.commit()
            if cur.rowcount:
                st.success("Password updated.")
            else:
                st.warning("User not found.")
    # Backup & restore
    st.markdown("—")
    st.subheader("Backups")
    # Download backup
    if st.button("Download Database (.db)", **PRIMARY_BTN_KW):
        with open("sacco.db", "rb") as f:
            st.download_button(
                "Save sacco.db",
                f.read(),
                file_name="sacco.db",
                mime="application/octet-stream",
                use_container_width=True,
            )
    uploaded_db = st.file_uploader(
        "Restore from .db (overwrites current DB)", type=["db"]
    )
    if uploaded_db and st.button("Restore Now", **PRIMARY_BTN_KW):
        with open("sacco.db", "wb") as f:
            f.write(uploaded_db.getbuffer())
        st.success(
            "Database restored. Restart the app to apply the new data."
        )

# -----------------------------------------------------------------------------
# Footer for all pages
# -----------------------------------------------------------------------------
draw_footer()
