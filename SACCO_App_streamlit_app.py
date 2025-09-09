import streamlit as st
import pandas as pd
import sqlite3
from datetime import datetime, date
from typing import Tuple, List, Dict

st.set_page_config(page_title="SACCO App", layout="wide")

# ---------- DB LIFECYCLE ----------

@st.cache_resource
def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect("sacco.db", check_same_thread=False)
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn

def init_db(conn: sqlite3.Connection) -> None:
    c = conn.cursor()
    # Members
    c.execute("""
        CREATE TABLE IF NOT EXISTS members (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            member_id TEXT NOT NULL UNIQUE,
            member_name TEXT NOT NULL,
            member_contact TEXT,
            email_address TEXT,
            registration_date TEXT NOT NULL
        );
    """)
    # Savings/Deposits
    c.execute("""
        CREATE TABLE IF NOT EXISTS savings_deposits (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            amount REAL NOT NULL CHECK(amount >= 0),
            date TEXT NOT NULL,
            transaction_id TEXT NOT NULL UNIQUE,
            member_id TEXT NOT NULL,
            interest_rate REAL DEFAULT 0,
            FOREIGN KEY(member_id) REFERENCES members(member_id) ON DELETE CASCADE
        );
    """)
    # Loans
    c.execute("""
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
    """)
    conn.commit()

conn = get_connection()
init_db(conn)

# ---------- HELPERS ----------

@st.cache_data
def load_members_df() -> pd.DataFrame:
    df = pd.read_sql_query(
        "SELECT id, member_id, member_name, member_contact, email_address, registration_date FROM members ORDER BY member_name COLLATE NOCASE ASC",
        conn
    )
    return df

def refresh_members_cache():
    load_members_df.clear()

def member_choice_map(df: pd.DataFrame) -> Tuple[List[str], Dict[str, str]]:
    if df.empty:
        return [], {}
    labels = [f"{row.member_name} (ID: {row.member_id})" for row in df.itertuples(index=False)]
    mapping = {labels[i]: df.iloc[i]["member_id"] for i in range(len(df))}
    return labels, mapping

def money(x: float) -> str:
    return f"UGX {x:,.2f}"

# ---------- UI ----------

st.title("SACCO App")

with st.sidebar:
    st.title("Navigation")
    page = st.radio(
        "Go to:",
        ["About", "Member Management", "Savings & Deposits", "Loan Management", "Financial Summary"]
    )

# ---------- ABOUT ----------
if page == "About":
    st.header("About This App")
    st.write(
        """
        The SACCO App helps members track savings, deposits, and loans with basic interest calculations.
        Data is stored locally in a SQLite database (sacco.db). Deleting a member will also delete their
        related transactions.
        """
    )
    st.info("Developed by Stephen Olet · Improved for reliability and safety.")

# ---------- MEMBER MANAGEMENT ----------
elif page == "Member Management":
    st.header("Member Management")

    with st.form("add_member_form", clear_on_submit=True):
        st.subheader("Add New Member")
        col1, col2 = st.columns(2)
        with col1:
            member_id = st.text_input("Member ID *").strip()
            member_name = st.text_input("Member Name *").strip()
            member_contact = st.text_input("Contact Information").strip()
        with col2:
            email_address = st.text_input("Email Address").strip()
            registration_date = st.date_input("Registration Date *", value=date.today(), max_value=date.today())
        submitted = st.form_submit_button("Register Member")

    if submitted:
        if not member_id or not member_name:
            st.error("Member ID and Member Name are mandatory.")
        else:
            try:
                conn.execute(
                    "INSERT INTO members (member_id, member_name, member_contact, email_address, registration_date) VALUES (?, ?, ?, ?, ?)",
                    (member_id, member_name, member_contact, email_address, registration_date.isoformat())
                )
                conn.commit()
                refresh_members_cache()
                st.success(f"Member {member_name} (ID {member_id}) registered on {registration_date.isoformat()}.")
            except sqlite3.IntegrityError as e:
                st.error(f"Could not register member: {e}")

    st.subheader("Registered Members")
    df_members = load_members_df()
    if df_members.empty:
        st.info("No members registered yet.")
    else:
        show = df_members.rename(columns={
            "id": "ID",
            "member_id": "Member ID",
            "member_name": "Name",
            "member_contact": "Contact",
            "email_address": "Email Address",
            "registration_date": "Registration Date"
        })
        st.dataframe(show, use_container_width=True)
        csv = show.to_csv(index=False).encode("utf-8")
        st.download_button("Download Members CSV", csv, "members.csv", "text/csv")

        st.divider()
        st.subheader("Delete Member")
        labels, mapping = member_choice_map(df_members)
        if labels:
            to_delete_label = st.selectbox("Select a member to delete", labels, key="delete_member_select")
            if st.button("Delete Member", type="primary"):
                mid = mapping[to_delete_label]
                try:
                    conn.execute("DELETE FROM members WHERE member_id = ?", (mid,))
                    conn.commit()
                    refresh_members_cache()
                    st.success(f"Member {to_delete_label} deleted (and related transactions, if any).")
                except sqlite3.Error as e:
                    st.error(f"Delete failed: {e}")
        else:
            st.info("No members available to delete.")

# ---------- SAVINGS & DEPOSITS ----------
elif page == "Savings & Deposits":
    st.header("Savings & Deposits")

    df_members = load_members_df()
    if df_members.empty:
        st.warning("Add members first in Member Management.")
        st.stop()

    labels, mapping = member_choice_map(df_members)
    member_selected_label = st.selectbox("Select a Member", labels, key="savings_member_select")
    member_id_selected = mapping[member_selected_label]

    with st.form("add_savings_form", clear_on_submit=True):
        st.subheader("Add to Savings")
        col1, col2, col3 = st.columns(3)
        with col1:
            savings_amount = st.number_input("Amount *", min_value=0.0, step=1000.0, value=0.0, help="UGX")
        with col2:
            savings_date = st.date_input("Transaction Date *", value=date.today(), max_value=date.today())
        with col3:
            interest_rate = st.number_input("Interest Rate for this deposit (%)", min_value=0.0, step=0.1, value=0.0)
        transaction_id = st.text_input("Transaction ID *").strip()
        submitted = st.form_submit_button("Update Savings")

    if submitted:
        if savings_amount <= 0:
            st.error("Amount must be greater than 0.")
        elif not transaction_id:
            st.error("Transaction ID is mandatory.")
        else:
            try:
                conn.execute(
                    "INSERT INTO savings_deposits (amount, date, transaction_id, member_id, interest_rate) VALUES (?, ?, ?, ?, ?)",
                    (float(savings_amount), savings_date.isoformat(), transaction_id, member_id_selected, float(interest_rate))
                )
                conn.commit()
                st.success(f"Added {money(savings_amount)} to {member_selected_label} on {savings_date.isoformat()} (Txn: {transaction_id}).")
            except sqlite3.IntegrityError as e:
                st.error(f"Could not save deposit: {e}")

    st.subheader("Member Savings History")
    df_savings = pd.read_sql_query(
        """
        SELECT s.amount, s.date, s.transaction_id, s.member_id, s.interest_rate, m.member_name
        FROM savings_deposits s
        JOIN members m ON s.member_id = m.member_id
        WHERE s.member_id = ?
        ORDER BY s.date DESC, s.id DESC
        """,
        conn, params=(member_id_selected,)
    )
    if df_savings.empty:
        st.info("No savings transactions yet.")
    else:
        view = df_savings.rename(columns={
            "amount": "Amount",
            "member_name": "Member Name",
            "date": "Date",
            "transaction_id": "Transaction ID",
            "member_id": "Member ID",
            "interest_rate": "Interest Rate (%)"
        })
        view.index = view.index + 1
        st.dataframe(view[["Amount", "Member Name", "Date", "Transaction ID", "Member ID", "Interest Rate (%)"]],
                     use_container_width=True)
        st.metric("TOTAL SAVINGS", money(float(view["Amount"].sum())))
        csv = view.to_csv(index=False).encode("utf-8")
        st.download_button("Download Savings CSV", csv, "savings.csv", "text/csv")

        st.divider()
        st.subheader("Delete Savings Transaction")
        txn_to_delete = st.text_input("Enter Savings Transaction ID to delete").strip()
        if st.button("DELETE Savings Transaction", type="primary"):
            if not txn_to_delete:
                st.error("Transaction ID is mandatory.")
            else:
                cur = conn.execute("DELETE FROM savings_deposits WHERE transaction_id = ?", (txn_to_delete,))
                conn.commit()
                if cur.rowcount:
                    st.success(f"Savings transaction {txn_to_delete} deleted.")
                else:
                    st.warning("No transaction found with that ID.")

# ---------- LOAN MANAGEMENT ----------
elif page == "Loan Management":
    st.header("Loan Management")

    df_members = load_members_df()
    if df_members.empty:
        st.warning("Add members first in Member Management.")
        st.stop()

    labels, mapping = member_choice_map(df_members)
    member_selected_label = st.selectbox("Select a Member", labels, key="loan_member_select")
    member_id_selected = mapping[member_selected_label]

    with st.form("loan_form", clear_on_submit=True):
        st.subheader("Apply for a Loan")
        col1, col2, col3 = st.columns(3)
        with col1:
            loan_amount = st.number_input("Loan amount *", min_value=0.0, step=10000.0, value=0.0)
        with col2:
            loan_period = st.number_input("Repayment period (months) *", min_value=1, max_value=60, value=12, step=1)
        with col3:
            loan_interest_rate = st.number_input("Interest rate (%) *", min_value=0.0, step=0.1, value=12.0)
        col4, col5 = st.columns(2)
        with col4:
            loan_date = st.date_input("Application date *", value=date.today(), max_value=date.today())
        with col5:
            loan_transaction_id = st.text_input("Loan Transaction ID *").strip()
        submitted = st.form_submit_button("Submit Loan Application")

    if submitted:
        if loan_amount <= 0:
            st.error("Loan amount must be greater than 0.")
        elif not loan_transaction_id:
            st.error("Loan Transaction ID is mandatory.")
        else:
            total_repayment = float(loan_amount) * (1.0 + float(loan_interest_rate) / 100.0)
            monthly_installment = total_repayment / int(loan_period)
            try:
                conn.execute(
                    """
                    INSERT INTO loans
                    (loan_amount, loan_period, total_repayment, monthly_installment, loan_date, loan_transaction_id, member_id, interest_rate)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (float(loan_amount), int(loan_period), total_repayment, monthly_installment,
                     loan_date.isoformat(), loan_transaction_id, member_id_selected, float(loan_interest_rate))
                )
                conn.commit()
                st.success(
                    f"Loan recorded for {member_selected_label}. "
                    f"Total repayment: {money(total_repayment)}, Monthly installment: {money(monthly_installment)}. "
                    f"Date: {loan_date.isoformat()} · Txn: {loan_transaction_id}."
                )
            except sqlite3.IntegrityError as e:
                st.error(f"Could not save loan: {e}")

    st.subheader("Member Loan History")
    df_loans = pd.read_sql_query(
        """
        SELECT l.loan_amount, l.loan_period, l.total_repayment, l.monthly_installment,
               l.loan_date, l.loan_transaction_id, l.member_id, l.interest_rate, m.member_name
        FROM loans l
        JOIN members m ON l.member_id = m.member_id
        WHERE l.member_id = ?
        ORDER BY l.loan_date DESC, l.id DESC
        """,
        conn, params=(member_id_selected,)
    )
    if df_loans.empty:
        st.info("No loan transactions yet.")
    else:
        view = df_loans.rename(columns={
            "loan_amount": "Loan Amount",
            "member_name": "Member Name",
            "loan_period": "Loan Period (mo)",
            "interest_rate": "Interest Rate (%)",
            "total_repayment": "Total Repayment",
            "monthly_installment": "Monthly Installment",
            "loan_date": "Loan Date",
            "loan_transaction_id": "Transaction ID",
            "member_id": "Member ID",
        })
        view.index = view.index + 1
        st.dataframe(
            view[["Loan Amount", "Member Name", "Loan Period (mo)", "Interest Rate (%)",
                  "Total Repayment", "Monthly Installment", "Loan Date", "Transaction ID", "Member ID"]],
            use_container_width=True
        )
        st.metric("TOTAL LOAN PRINCIPAL", money(float(view["Loan Amount"].sum())))
        csv = view.to_csv(index=False).encode("utf-8")
        st.download_button("Download Loans CSV", csv, "loans.csv", "text/csv")

        st.divider()
        st.subheader("Delete Loan Transaction")
        loan_txn_to_delete = st.text_input("Enter Loan Transaction ID to delete").strip()
        if st.button("DELETE Loan Transaction", type="primary"):
            if not loan_txn_to_delete:
                st.error("Transaction ID is mandatory.")
            else:
                cur = conn.execute("DELETE FROM loans WHERE loan_transaction_id = ?", (loan_txn_to_delete,))
                conn.commit()
                if cur.rowcount:
                    st.success(f"Loan transaction {loan_txn_to_delete} deleted.")
                else:
                    st.warning("No transaction found with that ID.")

# ---------- FINANCIAL SUMMARY ----------
elif page == "Financial Summary":
    st.header("Financial Summary of All Transactions")

    df_members = load_members_df()
    member_labels, mapping = member_choice_map(df_members)
    selection = st.selectbox("Select a Member to View Transactions:", ["All Members"] + member_labels)

    if selection == "All Members":
        df_savings = pd.read_sql_query(
            """
            SELECT s.amount, s.date, s.transaction_id, s.member_id, s.interest_rate, m.member_name
            FROM savings_deposits s
            JOIN members m ON s.member_id = m.member_id
            ORDER BY s.date DESC, s.id DESC
            """, conn
        )
        df_loans = pd.read_sql_query(
            """
            SELECT l.loan_amount, l.loan_period, l.total_repayment, l.monthly_installment,
                   l.loan_date, l.loan_transaction_id, l.member_id, l.interest_rate, m.member_name
            FROM loans l
            JOIN members m ON l.member_id = m.member_id
            ORDER BY l.loan_date DESC, l.id DESC
            """, conn
        )
    else:
        mid = mapping.get(selection)
        df_savings = pd.read_sql_query(
            """
            SELECT s.amount, s.date, s.transaction_id, s.member_id, s.interest_rate, m.member_name
            FROM savings_deposits s
            JOIN members m ON s.member_id = m.member_id
            WHERE s.member_id = ?
            ORDER BY s.date DESC, s.id DESC
            """, conn, params=(mid,)
        )
        df_loans = pd.read_sql_query(
            """
            SELECT l.loan_amount, l.loan_period, l.total_repayment, l.monthly_installment,
                   l.loan_date, l.loan_transaction_id, l.member_id, l.interest_rate, m.member_name
            FROM loans l
            JOIN members m ON l.member_id = m.member_id
            WHERE l.member_id = ?
            ORDER BY l.loan_date DESC, l.id DESC
            """, conn, params=(mid,)
        )

    # Savings Summary
    st.subheader("Savings & Deposits Summary")
    if df_savings.empty:
        st.write("No savings or deposit transactions found.")
    else:
        sv = df_savings.rename(columns={
            "amount": "Amount",
            "member_name": "Member Name",
            "date": "Date",
            "transaction_id": "Transaction ID",
            "member_id": "Member ID",
            "interest_rate": "Interest Rate (%)"
        })
        sv.index = sv.index + 1
        st.dataframe(sv[["Amount", "Member Name", "Date", "Transaction ID", "Member ID", "Interest Rate (%)"]],
                     use_container_width=True)
        total_savings = float(sv["Amount"].sum())
        st.markdown(f"**TOTAL SAVINGS: {money(total_savings)}**")

        manual_rate = st.number_input("Optional: Calculate interest on TOTAL SAVINGS (%)", min_value=0.0, value=0.0, step=0.1)
        if manual_rate > 0:
            st.markdown(f"**TOTAL Interest (manual): {money(total_savings * (manual_rate / 100.0))}**")

        # Delete Savings
        st.subheader("Delete Savings Transaction")
        del_s_txn = st.text_input("Savings Transaction ID").strip()
        if st.button("DELETE Savings Transaction", key="del_savings_summary", type="primary"):
            if not del_s_txn:
                st.error("Transaction ID is mandatory.")
            else:
                cur = conn.execute("DELETE FROM savings_deposits WHERE transaction_id = ?", (del_s_txn,))
                conn.commit()
                if cur.rowcount:
                    st.success(f"Savings transaction {del_s_txn} deleted.")
                else:
                    st.warning("No transaction found with that ID.")

    st.divider()

    # Loan Summary
    st.subheader("Loan Transactions Summary")
    if df_loans.empty:
        st.write("No loan transactions found.")
    else:
        ln = df_loans.rename(columns={
            "loan_amount": "Loan Amount",
            "member_name": "Member Name",
            "loan_period": "Loan Period (mo)",
            "interest_rate": "Interest Rate (%)",
            "total_repayment": "Total Repayment",
            "monthly_installment": "Monthly Installment",
            "loan_date": "Loan Date",
            "loan_transaction_id": "Transaction ID",
            "member_id": "Member ID",
        })
        ln.index = ln.index + 1
        st.dataframe(
            ln[["Loan Amount", "Member Name", "Loan Period (mo)", "Interest Rate (%)",
                "Total Repayment", "Monthly Installment", "Loan Date", "Transaction ID", "Member ID"]],
            use_container_width=True
        )
        st.markdown(f"**TOTAL LOAN PRINCIPAL: {money(float(ln['Loan Amount'].sum()))}**")

        # Delete Loan
        st.subheader("Delete Loan Transaction")
        del_l_txn = st.text_input("Loan Transaction ID").strip()
        if st.button("DELETE Loan Transaction", key="del_loan_summary", type="primary"):
            if not del_l_txn:
                st.error("Transaction ID is mandatory.")
            else:
                cur = conn.execute("DELETE FROM loans WHERE loan_transaction_id = ?", (del_l_txn,))
                conn.commit()
                if cur.rowcount:
                    st.success(f"Loan transaction {del_l_txn} deleted.")
                else:
                    st.warning("No transaction found with that ID.")
