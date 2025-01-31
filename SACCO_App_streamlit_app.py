# Financial Summary Page
elif page == "Financial Summary":
    st.header("Financial Summary of All Transactions")

    # Fetch Registered Members
    c.execute("SELECT member_id, member_name FROM members")
    members = c.fetchall()
    member_choices = ["All Members"] + [f"{member[1]} (ID: {member[0]})" for member in members]

    # Member Selection
    selected_member = st.selectbox("Select a Member to View Transactions:", member_choices)

    if selected_member == "All Members":
        # Fetch All Savings & Deposits Transactions
        c.execute("SELECT savings_deposits.*, members.member_name FROM savings_deposits JOIN members ON savings_deposits.member_id = members.member_id")
        savings_data = c.fetchall()
        df_savings = pd.DataFrame(savings_data, columns=["ID", "Amount", "Date", "Transaction ID", "Member ID", "Interest Rate", "Member Name"])

        # Fetch All Loan Transactions
        c.execute("SELECT loans.*, members.member_name FROM loans JOIN members ON loans.member_id = members.member_id")
        loan_data = c.fetchall()
        df_loans = pd.DataFrame(loan_data, columns=["ID", "Loan Amount", "Loan Period", "Total Repayment", "Monthly Installment", "Loan Date", "Transaction ID", "Member ID", "Interest Rate", "Member Name"])

    else:
        # Extract Member ID from Selection
        selected_member_id = members[member_choices.index(selected_member) - 1][0]

        # Fetch Savings & Deposits for Selected Member
        c.execute("SELECT savings_deposits.*, members.member_name FROM savings_deposits JOIN members ON savings_deposits.member_id = members.member_id WHERE savings_deposits.member_id = ?", (selected_member_id,))
        savings_data = c.fetchall()
        df_savings = pd.DataFrame(savings_data, columns=["ID", "Amount", "Date", "Transaction ID", "Member ID", "Interest Rate", "Member Name"])

         # Fetch Loans for Selected Member
        c.execute("SELECT loans.*, members.member_name FROM loans JOIN members ON loans.member_id = members.member_id WHERE loans.member_id = ?", (selected_member_id,))
        loan_data = c.fetchall()
        df_loans = pd.DataFrame(loan_data, columns=["ID", "Loan Amount", "Loan Period", "Total Repayment", 
                                                    "Monthly Installment", "Loan Date", "Transaction ID", "Member ID", "Interest Rate", "Member Name"])

    # Display Savings Summary
    st.subheader("Savings & Deposits Summary")
    if df_savings.empty:
        st.write("No savings or deposit transactions found.")
    else:
        # Reset index to start from 1 instead of 0
        df_savings.index = df_savings.index + 1

        # Reorder columns to move Member Name to the second column
        df_savings = df_savings[["Amount", "Member Name", "Date", "Transaction ID", "Member ID"]]
        st.dataframe(df_savings)

        # Calculate and display TOTAL SAVINGS
        total_savings = df_savings["Amount"].sum()
        st.markdown(f"**TOTAL SAVINGS: UGX {total_savings:.2f}**")

        # Manual option to calculate interest
        interest_rate = st.number_input("Enter interest rate for savings (%):", min_value=0.0, value=0.0)
        if interest_rate > 0:
            total_interest = total_savings * (interest_rate / 100)
            st.markdown(f"**TOTAL Interest to Date: UGX {total_interest:.2f}**")

        # Delete Savings Transaction
        st.subheader("Delete Savings Transaction")
        savings_transaction_id = st.text_input("Enter Savings Transaction ID to delete:")
        if st.button("DELETE Savings Transaction"):
            if not savings_transaction_id:
                st.error("Transaction ID is mandatory.")
            else:
                c.execute("DELETE FROM savings_deposits WHERE transaction_id = ?", (savings_transaction_id,))
                conn.commit()
                st.success(f"Savings transaction with ID {savings_transaction_id} has been successfully deleted.")

    # Display Loan Summary
    st.subheader("Loan Transactions Summary")
    if df_loans.empty:
        st.write("No loan transactions found.")
    else:
        # Reset index to start from 1 instead of 0
        df_loans.index = df_loans.index + 1

        # Reorder columns to move Member Name to the second column and add Interest Rate next to Loan Period
        df_loans = df_loans[["Loan Amount", "Member Name", "Loan Period", "Interest Rate", "Total Repayment", 
                             "Monthly Installment", "Loan Date", "Transaction ID", "Member ID"]]
        st.dataframe(df_loans)

        # Calculate and display TOTAL LOAN
        total_loan = df_loans["Loan Amount"].sum()
        st.markdown(f"**TOTAL LOAN: UGX {total_loan:.2f}**")

        # Delete Loan Transaction
        st.subheader("Delete Loan Transaction")
        loan_transaction_id = st.text_input("Enter Loan Transaction ID to delete:")
        if st.button("DELETE Loan Transaction"):
            if not loan_transaction_id:
                st.error("Transaction ID is mandatory.")
            else:
                c.execute("DELETE FROM loans WHERE loan_transaction_id = ?", (loan_transaction_id,))
                conn.commit()
                st.success(f"Loan transaction with ID {loan_transaction_id} has been successfully deleted.")

# Closing the SQLite connection on app termination
conn.close()
