# ELEv8 reception training | Queries and tickets

## 1. Open, work and close a ticket

1. Search for the member and check for an existing open ticket about the same issue. Add a dated note to that ticket if the issue is already being handled. Open **Add Query** on the member profile for a linked ticket, or **Queries → New Query** for a prospect or walk-in. After saving a linked ticket, open it from the member’s Queries section.
2. Complete the intake fields below. Save and check the generated query reference, linked member, actual department, owner, priority and follow-up date. The saved assignment can differ from the choices entered on the form.
3. Check the evidence named in the relevant type row. Record the finding, who is doing the next step, and when Reception will update the client. Use **Update Query** for the current status, priority, assignment, dates and **Action Taken**. Use **Add Note** for a dated entry in the activity timeline.
4. If Management needs to intervene, use **Escalate** and enter the facts, what has already been checked, the decision needed and the member’s deadline. This moves the ticket to Management, makes it urgent and sets follow-up to today. For a controlled decision, use the applicable **Request Manager Approval** or signed workflow action. The general approval form captures the reason and, where shown, the proposed discount. The authorised reviewer records **Approve** or **Reject** and the decision notes.
5. For ordinary tickets, communicate the outcome, then use **Close Query**. Select a closure reason and enter resolution notes with the action or answer, evidence reference, date, and what the member was told. For an owing account where the closure screen requires it, confirm the member was informed about the outstanding amount. A pending approval or unfinished signed workflow blocks ordinary closure. For signed cases, use the terminal workflow action in section 3, which closes the original ticket.

### New Query fields

| Field | How to fill it in |
|---|---|
| Linked member | Verify identity and select the correct profile. Required for cancellation and for all six signed workflows. To repair an unlinked open ticket, use **Match this query to the client’s member record**, enter the verified member record ID and identity matching evidence, then select **Link verified member**. |
| Member Name and Contact Number | Shown without a linked profile. Enter the verified name and callback number for a prospect, walk-in, or member awaiting a match. |
| Category | Required. Choose the category for the outcome the person is asking for. |
| Query Type | Select the exact dropdown option from section 4. Use **Other** in the closest category only when none of its named options fit. |
| Description / Notes | Required. Put the request, dates and amounts, verified facts, evidence references, action already taken and requested outcome here. Section 4 lists the facts to capture for each type. Use the secure evidence workflow for bank statements, identity documents and signatures. |
| Priority | Low for routine information, Medium for normal follow-up, High for significant impact or a time-sensitive dispute, Urgent for active safety issues or immediate management action. Medium is the form default. With a linked account the system can suggest higher priority, including for Incident, Complaint, Refund and older arrears. Set Urgent explicitly for an urgent walk-in incident. |
| Assigned to | Select a named staff member where routing permits. In ELEv8, confirm the saved owner and record the operational handoff in the notes when the queue is shared. |
| Department | Reception, Sales, Admin, Collections, Management, Maintenance or PT. Confirm the saved value after automatic routing. |
| Due Date | Date the action or decision is due. Use the actual booking, billing, safety or provider deadline when applicable. |
| Follow-up Date | Date Reception or the owner next checks the case or contacts the person. Blank defaults to five working days. Set an earlier date when the case requires it. |
| Membership Snapshot | Read the linked account’s membership, access and contract figures. Check the underlying records when any figure is uncertain. |
| Payment / Arrears Decision | For relevant linked financial cases, read **Total Outstanding, Arrears Months, Discount Rule, Write-off Amount, Settlement Amount, Payment Method, PTP Required, POP Required, Bank Statement, Income Verified, Access Status, Access Decision, Follow-up, Assigned To, Open Queries, Priority** and **Recommended Action**. These are readouts. In Payments & Arrears and Collections / PTP, where offered, enter **Proposed discount %** and **Reason / note for the manager**. Blank discount uses the recommended percentage. Explain the basis and what the member can pay. |
| Cancellation Details | Enter the linked member, contract length, months completed, monthly fee, payment method and agreed first payment date as described in section 3. |

**ELEv8 routing on save.** Refund goes to Management with Ayrton when matched. Sales, Membership Renewal, Upgrade/downgrade and Reactivation go to Sales with Thato when matched, with Ayrton notified. Complaint goes to Management and the configured approving manager if available. Other categories initially go to the shared Admin queue. Later signed-workflow actions can change the owner and department. The operational handoffs in section 4 describe who needs to do the work. Check the saved assignment and have the receiving team acknowledge shared-queue work.

**Status and closure.** Use **Open** when captured, **In Progress** while working, **Pending Member** when waiting for the client, and **Pending Admin** when waiting for an internal action. **Pending Approval** and **Closed** are set by the system. **Escalated** is the management intervention state. **Resolved** is a terminal status in the application, so use **Close Query** directly when you need a closure reason and a full record. Payments & Arrears and Collections / PTP also offer **Arrears Confirmed, Discount Offered, PTP Created, Awaiting Payment, POP Received, Payment Verified, Access Blocked** and **Access Restored**. Choose the status that reflects confirmed facts.

| Close Query field | How to fill it in |
|---|---|
| Closure reason | Choose **Resolved — action completed**, **Resolved — information given, no action needed**, **Member unreachable**, **Withdrawn by member**, or **Duplicate query**. For unreachable cases, note attempts and dates. For a duplicate, record the active reference. |
| Resolution notes | State what happened, evidence or transaction/reference ID, any approval and action date, and how the member was told. |
| Outstanding balance acknowledgment | Where shown, tick only after explaining the outstanding amount and how it must be settled or included. |

## 2. Collections / Promise to Pay (PTP)

1. Link the member. Confirm the ledger, arrears periods and amount, recent receipts, any existing PTP, usable mandate, bank statement readiness and actual access status. Use **Payments & Arrears** for a balance, receipt or debit enquiry. Use **Collections / PTP** for a promise, settlement, instalment arrangement or follow-up on one.
2. Use the current arrears tier on the query. The query calculator shows 0% for fewer than two months, 25% for two to three, 35–50% for four to five, 60–75% for six to eight, and manager discretion from nine months. Above 50% or from nine months needs manager approval. An approved query discount and the PTP’s own payment, affordability and mandate checks are separate steps.
3. Open **Create PTP** on the ticket where available. In the member’s PTP form enter **PTP agreed by** (phone, walk-in or other), **Amount Promised**, **Promise Date**, **Upfront Amount Agreed**, **Number of Monthly Payments**, **First Collection Date**, **Payment Method**, **Arrangement Type**, **Unblock Access?**, **Unblock Until** if applicable, **Auto-block if PTP fails?**, **Discount %**, **Consultant Notes** and **Requires manager approval** where applicable. **Amount Owing** is read-only. Confirm the amount and schedule against the member’s actual commitment. The PTP screen calls for verified bank-statement evidence, approved DebiCheck as required, and an automatic plan of at least R150/month and at most 12 months. Exceptions go to a manager.
4. Save and confirm the linked PTP ID. Record contact attempts under **Log Contact Attempt** with method, call code, next follow-up date and notes. For a receipt, enter **Amount Paid, Payment Date, Payment Method, Reference** if available, linked **PTP**, and the **receipt file**. An authorised verifier checks the actual payment and correct allocation. A receipt on file represents **POP Received** until verification. Check whether access may change under the current rules and confirm the actual result before reporting access restored.
5. Follow the linked outcome. A new PTP sets **PTP Created**, partial payment sets **Awaiting Payment**, full payment sets **Payment Verified**, and a broken PTP escalates the ticket with High priority and follow-up today. A superseded or declined arrangement returns the ticket to **In Progress**. After a break, verify the payment history and auto-block setting, contact the member, and record the authorised recovery or replacement. Close when the issue and any outstanding follow-through are complete. A payment-verified ticket can still need a separate access or ledger check.

## 3. Signed request workflows

The six forms are **Refund, Freeze, Complaint, Cancellation, Banking / DebiCheck request**, and **Payments & Arrears → Proof of payment**. Open **Signed request and workflow** on the linked ticket and choose **Create secure member form link**. Check the communication history and confirm the member received the link through an approved channel. A link expires in seven days. **Replace secure member form link** disables an earlier unsubmitted link. An admin or manager can assign a Reception verifier by selecting the reviewer and entering an assignment reason.

The member confirms their displayed details or enters a correction, fills in the fields below, acknowledges the notice and draws a signature. The form quotes 5–10 days for processing and tells the member they will receive an outcome. Uploads accept up to five PDF, PNG or JPEG files at 5 MB each. Reception checks the latest signed submission and retained evidence, enters **Findings / reason**, ticks **I verified the signed request and its evidence**, and uses **Confirm verification and hand off**. If evidence is incomplete, use **Request correction / information** with the specific missing item, then issue a replacement link. Where a decision is required, the designated reviewer enters findings and approves or rejects. Verification and final review are performed by different people. Rejection closes the ticket and queues a member outcome.

Review **Client and staff communications** for failed or stalled messages. Confirm the transport did not already send a stalled message before retrying, and correct missing contact details on the member profile. Queued WhatsApp and transport acceptance do not establish delivery. Record the actual member contact.

### A. Proof of payment

1. Capture payment date, amount, method, reference and the charge or period it was meant to cover. The member enters the optional payment reference and uploads proof.
2. Match the receipt against the bank/provider and member ledger with the authorised payment verifier. Check payer/reference, date, amount, correct member and period, and duplicate posting. If the credit is missing or unidentified, record the discrepancy and follow-up date and request what is needed. Use the payment or collections workflow for any posting or reallocation.
3. Reception records verification and hands off to the designated reviewer. After approval, confirm the actual payment outcome and revised balance or PTP status, tell the member, enter **Outcome / communication notes**, tick the communication confirmation and select **Record completion**. This closes the ticket. The signed form retains the evidence and review trail. Ledger reconciliation is a separate step.

### B. Refund

1. Enter **Preliminary eligibility findings** and select **Confirm eligibility** before issuing the link. Record the payment, why it may qualify and what you have checked. The member chooses **Incorrect debit, Double debit, Debit after contract ended, Debit after cancellation, Paid at Reception and debit also ran**, or **Other refund reason**, selects once/one month or twice/two months, and uploads a bank statement showing the payment.
2. Enter **Verified refundable amount**, **Collected payment record IDs** separated by commas, and **Findings / reason**. Use **Check refund against records**. Confirm successful collection, no earlier refund or reversal, and verified originating account. For double debit, confirm two distinct successful source references. For post-cancellation, verify cancellation was effective before the debit. For contract expiry, enter the verified signed end date and contract reference, and check for renewal. For Reception plus debit, enter the obligation evidence reference and show that both payments covered the same charge. The amount must be supported by the selected receipts. Accounting adjustments and receipts already claimed by another refund case are excluded.
3. The configured reviewer records approval or rejection. The configured refund processor performs the approved refund to the verified originating account, then selects **Record payment made** with the exact approved amount, actual payment date, payment reference and confirmation file. An amount change requires fresh approval. The software records the payment evidence after execution.
4. Communicate the result and record it with **Outcome / communication notes** and the confirmation checkbox. Select **Record completion** and check that the original ticket is closed.

### C. Freeze

1. Record the requested period, reason, balance and actual billing schedule. The member enters **Reason** (Medical, Holiday/travel, Financial, Exams, Employment, Study travel or Other), **Requested start**, **Requested end**, and uploads supporting evidence. For exams, use a timetable showing the member’s details. For employment or study travel, use the appropriate employer or organisation letter.
2. Verify evidence, arrears and eligibility. Enter **Actual payment due date**, **Next actual payment date** and **Source payment schedule / record reference**. The system uses the signed submission date against the 14-day cutoff. A submission on or before the cutoff can use the current due cycle. A later submission moves to the next actual cycle. The applied start is the later of the requested start and the effective cycle.
3. The designated manager approves or rejects. On approval, the manager completes the billing/provider action, enters the **Completed billing/provider freeze reference** and **Final outcome and approved dates**, then selects **Record executed freeze and complete query**. Resolve date conflicts, an inactive membership or an overlapping freeze before execution.
4. Confirm the member was informed of the actual dates and billing outcome. The app schedules the member status for the approved period and restores the prior status after the period when applicable. Check the scheduled dates and closure. For an extension, capture the original freeze reference and coordinate dates with Management before a second period is recorded.

### D. Cancellation and fees

1. Link the member. Enter **Contract Type** (12, 24 or 36 months), **Months Completed** (0 through contract length), **Monthly Fee (R)** from the signed agreement, and **Payment Option** (cash upfront, six instalments on existing debit order, or six instalments on a new debit order). For instalments, enter the **Agreed First Payment Date**, today or later, and check mandate, bank statement and income readiness. Review the read-only **Contract Start**, quote preview and full schedule. If the quote needs correction, save a new version with the corrected inputs and a **Reason for revision**, then use the current version for acceptance.

| Term | Penalty months in the quote calculator |
|---|---|
| 12 months | 0–5 completed → 4; 6 or more → 2 |
| 24 months | 0–11 completed → 6; 12–17 → 4; 18 or more → 2 |
| 36 months | 0–17 completed → 6; 18–26 → 4; 27 or more → 2 |

2. The quoted fee is penalty months × monthly fee. The calculator adds the positive outstanding balance once. On the combined amount, the cash/six-instalment discounts are **0%/0% below R2,000**, **20%/10% from R2,000 through R3,000**, and **35%/20% above R3,000**. Check the contract and account against the quote before presenting it. Refer expiry or discretionary waiver questions for a decision and retain the basis of any agreed correction.
3. Issue the secure form. The member reviews the saved quote, chooses **Cash settlement** or **Six monthly instalments**, acknowledges, signs and may upload supporting documents. Verify the returned acceptance and agreed dates. If a new or changed debit is required, have the authorised operator arrange it through the mandate workflow.
4. Track qualifying successful receipts after signed acceptance. For a same-day receipt without a timestamp, select the qualifying receipt IDs and enter **Receipt / timing evidence** before counting them. Check duplicate receipts, adjustments and refunded amounts. Cash requires full settlement. Instalments require all six payments and review seven days after the later of the final scheduled due date and the last qualifying payment.
5. Review the access register for the full period since acceptance, including any coverage outside the app. Investigate any entry found. Enter **Access register source and dates reviewed**, confirm non-attendance, enter **Reception’s final payment and attendance findings**, and use **Confirm settlement and send to Mvuleni**. The manager handoff carries a seven-day deadline.
6. The designated manager checks the settlement and attendance, confirms cancellation of all applicable debit orders with the provider, including externally managed orders, and records **Provider cancellation reference / verified no-active-order evidence**, **Provider outcome** and the outcome confirmation. The final action sets the member to **Cancelled** and closes the ticket. Confirm the outcome reached the member. If a card was issued and returned, record the member, linked cancellation, card serial and reusable condition in Reception Admin’s card-return workflow.

**Cancellation screen differences.** Intake guidance says Reception chooses the option and the member does not choose again, while the signed form displays an option selector. Check the signed selection against the agreed terms before acting. Use the signed workflow for settlement, attendance and mandate checks even if the details screen offers a separate cash cancellation control. The quote editor has no discretionary waiver amount field. For a waiver requiring different terms or a signed request that is withdrawn or abandoned before settlement, have Management record the disposition and confirm the applicable exception path. The ordinary Close Query action requires the signed workflow to be terminal.

### E. Banking / DebiCheck request

1. Select **First-time banking details** or **Banking details change**. The member enters **Account holder, Bank, Account type, Account number** and **Branch code**. For a change, a statement is required even though the common upload label says supporting documents are optional. Verify at least two months of statement coverage and enter **Statement coverage from** and **through**. Check payer identity and authority securely.
2. Verify the signed fields. In **New DebiCheck**, confirm member and payer type, alternate payer name/SA ID where applicable, account holder, bank, account type and number, branch code, instalment amount, member/NuPay reference, staff number, authentication type, first debit date, frequency, number of instalments, tracking and identity type with passport/juristic details where applicable. Use the signed details and agreed payment terms. Save and follow the authorised NuPay submission and member authorisation process.
3. Enter **New mandate ID** and, for a change, the distinct **Old mandate ID**. The linked mandate must match the signed account number, type and branch code. Wait for confirmed provider approval and use **Check confirmed mandate status** where needed. A push is a submission.
4. First-time banking completes after approval is confirmed. For a change, keep the old order in place while approval is pending. After approval, the designated manager confirms the old order’s provider status is cancelled/inactive, records **Provider confirmation reference** and **Outcome notes**, and selects **Record confirmed old-order cancellation**. Check the closed ticket and confirm the outcome to the member.

### F. Complaint

1. Record who raised it, date/time, location, what was said or observed, relevant staff or service, witnesses or records, immediate action and the desired remedy. The member answers **What happened?**, signs and may upload supporting documents.
2. Review the returned submission and verify the facts and evidence. Record the findings and send to the designated manager. The manager records the decision and remedy, requests more information or rejects with reasons.
3. Carry out the agreed remedy, tell the member the decision, enter **Outcome / communication notes**, confirm communication and select **Record completion**. Use a linked Incident ticket if there is a separate safety response to track. For a walk-in or prospect with no member profile, the signed form is unavailable. Keep the complaint with Management for investigation and recorded follow-up. If the person is an existing member, match their profile. Ordinary closure of a Complaint-category ticket is blocked by the signed-workflow requirement, so a genuine non-member closure needs an agreed Management handling path.

## 4. Every category and query type

The middle column gives the type-specific details to enter in **Description / Notes**, in addition to the intake fields. The last column gives the operational handoff and the point at which the case is ready to close. Record evidence references in the ticket and use authorised member, payment, sales, maintenance or provider records to carry out changes. These handoffs are training guidance for the shared queue.

### Membership

| Query Type | Capture and verify | Action through closure |
|---|---|---|
| Membership status | Displayed status, date it changed, what the member expected and any hold reason. | Check the profile and status history. If wrong, Admin corrects it and Reception confirms the resulting status and explains it. |
| Access status | Current access status, last denial, arrears, verification and mandate state. | Explain the verified reason. If a live card/reader or arrears issue remains, track it under Access or Payments/Collections as appropriate and cross-reference. |
| Contract term / end date | Signed contract reference, start date, term and any amendment or renewal. | Reconcile the snapshot with the signed terms. Admin resolves discrepancies. Confirm the actual end date and remaining term to the member. |
| Membership package | Current package, fee, inclusions and any pending package change. | Check the signed plan and pricing. Explain the current package or refer a mismatch to Admin for correction. |
| Renewal | Contract end, outstanding balance, desired package and start date. | Sales checks eligibility and terms, records accepted renewal or the member’s decision, and confirms effective date and charges. |
| Upgrade/downgrade | Current and requested package, price difference, requested date and debit impact. | Sales confirms terms and acceptance, then Admin/billing applies the change where required. Verify package and next charge. |
| Transfer | Current and proposed member, requested date, signed consent and secure new-member ID reference. | Admin obtains manager approval, then verifies completed transfer, affected account records and notification. |
| Dependent account | Main member, dependent, relationship, consent, requested start and main account standing. | Admin validates eligibility and records the relationship. Confirm dependent status and access. |
| Guardian/payer update | Existing and proposed guardian/payer, authority/consent and effective date. | Admin verifies authority. Open linked Banking work if details or mandate change. Confirm the payer record and new mandate state before reporting the switch. |
| Member details update | Fields to change, verified identity and effective date. | Update the authorised profile fields and confirm the corrected values. Use the Banking workflow for bank details. |
| Freeze/suspension | Reason, requested dates and outstanding balance. | Explain eligibility and timing for an enquiry. For an actual application, open or locate a linked **Freeze** ticket and give the member its reference. |
| Cancellation information | Contract term, end date, balance and what the member wants explained. | Provide verified options and fee basis. For a submitted request, open or locate a linked **Cancellation** ticket. |
| Reactivation | Previous status, balance, card status, desired start and proposed package. | Sales checks arrears and terms, records acceptance or decline, and confirms the actual membership and access result. |
| Other | Specific membership question and source record. | Reception answers from the verified profile or asks Admin to complete the named correction. Record the result. |

### Payments & Arrears

| Query Type | Capture and verify | Action through closure |
|---|---|---|
| Debit order failed | Amount, due date, return reason, mandate status and previous retries. | Collections confirms the failure and authorised recovery method. Record any re-presentation or alternative payment and its result. |
| Debit order date change | Current and requested debit date, mandate, effective cycle and any payment already scheduled. | Billing checks what can be changed and obtains any required mandate consent. Confirm the provider date and next charge. |
| Arrears balance | Amount, periods, charges, credits and recent receipts. | Reconcile and explain the balance. If an arrangement is needed, open or locate **Collections / PTP** and link the reference. |
| Statement request | Statement period, account and verified delivery destination. | Retrieve the correct statement, send through the approved channel and record the period and delivery. |
| Proof of payment | Amount, date, method, payment reference and purpose. | Follow **3A** through actual matching, authorised ledger action, review and closure. |
| Double debit | Both dates, amounts, transaction references and the charge they covered. | Collections verifies both debits and any reversals. If money is requested back, open a linked **Refund** ticket. Confirm findings and give the refund reference where applicable. |
| Annual levy | Levy period, posted date, amount and signed package terms. | Explain the verified charge or refer an incorrect posting to billing. Confirm correction or open a Refund ticket if repayment is requested. |
| Joining fee | Signed joining terms, amount, receipt and posting reference. | Verify the charge and allocation, correct or escalate any mismatch, and confirm the outcome. |
| Debit order dispute | Exact amount and date disputed, reason, mandate reference and any reversal/dispute reference. | Collections checks authority, transaction and reversal history. Record the decision and verified ledger result, and cross-reference any refund. |
| Access blocked due to arrears | Denial reason, balance, recent receipts and PTP state. | Collections verifies what is needed to restore access. Record the payment or arrangement separately and confirm actual access after the authorised action. |
| Other | Payment event, date, amount, reference and requested fix. | Collections reconciles the specific item and confirms the outcome or links the separate action needed. |

### Collections / PTP

| Query Type | Capture and verify | Action through closure |
|---|---|---|
| New PTP arrangement | Balance, affordability, amount promised, date, readiness and any existing PTP. | Use **section 2** to create and link the actual plan, then track verified fulfilment or escalation. |
| Once-off settlement | Exact proposed amount, date, method and applicable discount/approval. | Agree authorised terms through section 2. Verify full receipt and settled balance before closure. |
| Split payment arrangement | Agreed payments and dates, upfront amount, affordability and method. | Use section 2 for the schedule and track each receipt. Confirm final allocation and balance. |
| Premium top-up arrangement | Current premium, proposed extra amount, affordability and schedule. | Use section 2, verify the agreed mandate and collection schedule, and track the top-ups until the arrangement is fulfilled or replaced. |
| Add arrears to back of contract | Arrears, current term, proposed extension and expected effect on dates and charges. | Collections reviews eligibility and consent. Record the approved arrangement, verify the updated term and schedule, and keep follow-up for any remaining obligations. |
| Broken PTP | PTP ID, missed amount/date, last contact and auto-block setting. | Verify payment and access state, contact the member, and track authorised recovery or a linked replacement. Close after the break is dealt with and next steps are recorded. |
| PTP follow-up | PTP ID, promised date and amount, verified receipts and next balance. | Contact the member, log the result and update follow-up. Close once fulfilment or the agreed next outcome is confirmed. |
| Discount / settlement request | Arrears age, proposed percentage, settlement amount, offer expiry and member commitment. | Use the permitted tier and obtain required approval. Record acceptance or decline. If accepted, use section 2 and confirm settlement. |
| Access blocked due to arrears | Reason shown, PTP/receipt reference, approval and temporary-access expiry if any. | Check PTP and collections eligibility, get authorised access action if applicable, and confirm the real access result. |
| Other | Outstanding amount, collection issue, agreement/reference and member’s requested next step. | Collections takes ownership, records the agreed action and checks the linked plan or ledger before closure. |

### Access, including TCP or turnstile issues

For gate cases capture exact scan time, entrance/reader, affected member, masked credential reference, and the displayed or logged decision and reason. Record whether one member or several are affected. A credential can be unmatched, or a matched member can be denied because of membership status, the live Collections rule, a block or expired temporary access. Use the evidence to separate a credential issue, an account decision and a possible reader or connection fault.

| Query Type | Capture and verify | Action through closure |
|---|---|---|
| Card not working | Card reference, scan time, reader, device response, scope and active status. | Check the credential link and gate event. Route account holds to the appropriate owner and reader failures to the technical owner. Confirm a successful authorised retest. |
| Lost card | Last known use, card reference, member identity and replacement request. | Have the authorised operator revoke the old credential and issue/link the replacement as applicable. Confirm old card disabled and replacement tested. |
| PIN/QR code issue | Credential format, device/app and error, with codes kept out of notes. | Verify identity and credential state, have the authorised owner reset or reissue it, then test. |
| Access blocked | Exact gate reason, status, block or expiry, compliance and ledger findings. | Resolve the underlying hold through its owner. Verify the gate result before closing. |
| Member photo issue | Profile and photo issue, where it appears and whether identity is verified. | Update through the authorised profile workflow. Confirm correct photo appears at check-in. |
| Unauthorized access | Date/time, entrance, event or footage reference, affected account and immediate action. | Secure the credential through an authorised owner and notify Management. Log a linked Incident when safety or security follow-up is needed. Confirm containment and record the outcome. |
| Entry denied due to arrears | Scan result, balance, PTP status and last verified payment. | Ask Collections to confirm the rule and authorised next step. Verify access after the underlying condition is addressed. |
| Other | For a TCP/reader outage, list entrance/device, start time, affected scope, connection symptom and event/error reference. | Hand off to the technical owner, track service restoration and confirm an authorised end-to-end scan. |

### Compliance

| Query Type | Capture and verify | Action through closure |
|---|---|---|
| Missing ID copy | Whose ID is required, profile and existing document status. | Request it through the authorised channel, verify and file it, then confirm the compliance flag. |
| Missing bank statement | Payer, required coverage period and secure document status. | Request and verify required coverage, file securely, and confirm the compliance or qualification result. |
| DebiCheck not approved | Mandate reference, actual provider status, last push and authorisation attempts. | Check provider status, guide the member to authorise or have the mandate owner correct/retry as applicable. Confirm final result. |
| Contract not signed | Contract version, member/application, missing signer and link status. | Send or reissue the correct signing request and confirm retained signed terms. |
| POS not captured | Sale/payment date, amount, tender, reference and missing entry. | Verify the transaction. The authorised owner records or reconciles it once and confirms the correct reference. |
| Underage approval | Age/eligibility evidence reference, guardian relationship and consent status. | Admin collects required consent and obtains authorised approval. Record decision and resulting membership status. |
| Incomplete profile | Exact missing fields/documents and the stage blocked. | Securely collect and verify the missing information, complete the profile and confirm readiness. |
| Other | Required rule, missing evidence and process affected. | Admin verifies and clears the item or documents the decision and remaining restriction. |

### Cancellation

| Query Type | Capture and verify | Action through closure |
|---|---|---|
| Cancellation request | Member, contract, balance, reason, request date and quote inputs. | Follow **3D** through signed acceptance, settlement, attendance review and confirmed provider action. |
| Early cancellation | Completed months, signed terms, requested end date, fee and arrears. | Follow **3D**. Explain the saved quote and keep open until the terminal cancellation action. |
| Penalty waiver | Requested waiver, quoted fee, grounds and supporting records. | Refer the specific decision to Management. Record written outcome and the correct quote before signing or re-signing, then follow **3D** for a continuing cancellation. |
| Cancellation follow-up | Original ticket, quote, signed stage, receipts and mandate status. | Update the existing cancellation where possible. Tell the member the verified stage and next checkpoint. Finish on the original case through **3D**. |
| Other | Specify the cancellation issue and any existing reference. | Explain the verified position or work the actual cancellation in **3D**. Capture the final decision and evidence. |

### Freeze

| Query Type | Capture and verify | Action through closure |
|---|---|---|
| Medical freeze | Requested dates and secure reference to supporting evidence. | Follow **3C** through effective-cycle check, decision, billing execution and confirmed dates. |
| Holiday freeze | Travel or holiday dates and supporting evidence. | Follow **3C** and confirm the actual approved period and billing effect. |
| Financial freeze | Dates, actual balance and supporting evidence of the circumstances. | Follow **3C**. Record the arrears handling and approved dates or refusal. |
| Freeze extension | Existing freeze reference, end date, proposed extension and new evidence. | Follow **3C** and have Management resolve overlaps and confirm the resulting schedule. |
| Other | Specific reason, dates and evidence. | Use the closest reason in **3C** and document the manager’s decision and execution. |

### Refund

| Query Type | Capture and verify | Action through closure |
|---|---|---|
| Overpayment refund | Expected charge, payments, amount overpaid and ledger position. | Follow **3B** and establish which receipts covered the same obligation before approval. |
| Cancellation refund | Cancellation reference, effective date, debit date, amount and mandate evidence. | Follow **3B**, checking that cancellation completion preceded the disputed debit. |
| PT refund | PT booking/package, payment, service delivered and basis for refund. | PT confirms service facts and the payment verifier confirms the receipt. Follow **3B** for the financial decision and payout. |
| Levy refund | Levy period, amount, agreement/eligibility and actual collection. | Verify why the levy is refundable and follow **3B**. |
| Admin error refund | Incorrect entry, correct treatment, actual payment and any previous reversal. | Establish the real collected amount and refund basis, then follow **3B**. |
| Other | Refund basis, actual receipt(s), requested amount and supporting evidence. | Follow **3B** and record the reviewer’s specific decision and outcome. |

### Banking / DebiCheck request

| Query Type | Capture and verify | Action through closure |
|---|---|---|
| First-time banking details | Member, payer relationship, current mandate position and first collection need. Use the secure form for account details. | Follow **3E**. Confirm the new mandate’s provider approval and completion. |
| Banking details change | Member, verified payer, existing mandate reference and requested change date. Use the secure form for account details and statement. | Follow **3E**. Verify statement coverage, approve the replacement through the provider, then confirm cancellation of the old order. |

### Sales

| Query Type | Capture and verify | Action through closure |
|---|---|---|
| Pricing | Package sought, prospect/contact and pricing date or quote reference. | Sales gives current approved terms and records response, lead or next follow-up. |
| Joining requirements | Package, age/payer situation and outstanding joining requirements. | Sales explains the verified checklist and captures application handoff or answer. |
| Promotion | Offer name/source, dates and eligibility facts. | Verify terms, apply the valid offer in Sales if accepted, and confirm outcome. |
| 7-day voucher | Voucher reference, issue date, proposed start and visitor/contact. | Sales checks eligibility and validity, records issue or redemption as applicable, and confirms expiry and next step. |
| DebiCheck help | Application/mandate reference, provider status and blocker. | Help with the approved authorisation steps. Have the mandate owner resolve errors and confirm status. |
| Contract signing | Application, version, signer and link status. | Send or replace the correct signing request. Confirm signature receipt or record the outstanding follow-up. |
| Lead follow-up | Lead reference, last contact, interest and promised callback. | Sales records attempt and outcome in the lead record. Close the ticket once the action or handoff is recorded, with any remaining sales follow-up dated. |
| Declined membership | Application, recorded decision and member’s question or requested review. | Sales/Management checks the decision, records any authorised review and communicates the approved response. |
| Other | Sales request, offer/application reference and next decision. | Route to Sales and record the answer, decision or tracked handoff. |

### Facility

| Query Type | Capture and verify | Action through closure |
|---|---|---|
| Equipment broken | Equipment ID, location, fault, time and any injury risk. | Secure unsafe equipment, notify Maintenance/Management, track repair and confirm safe return or removal. |
| Weights missing | Type, size, quantity, location and time last seen. | Check inventory and floor, replace or escalate the loss, and confirm the final count/action. |
| Aircon/fans issue | Unit/zone, symptom, time and areas affected. | Assign maintenance, verify service restored or record the approved mitigation and handoff. |
| Lights/electricity | Area, circuit/equipment symptom, outage start and danger. | Secure affected area, escalate as needed, and confirm safe service restoration. |
| Generator issue | Time, outage impact, generator symptom and zones affected. | Hand off to the authorised technical owner and confirm restoration/test. |
| Water issue | Outlet/area, leak or supply issue and impact. | Isolate danger, notify maintenance, and confirm water service or leak correction. |
| Toilet issue | Specific toilet, blockage/leak/damage and availability. | Arrange cleaning/maintenance, close the affected unit if needed and verify usable condition. |
| Cleaning complaint | Area, issue, time and immediate hygiene risk. | Assign cleaning and verify completion. If a formal remedy is sought, cross-reference a Complaint ticket. |
| Safety hazard | Hazard, exact location, people affected, time and controls taken. | Secure the area and notify Management immediately. Use a linked Incident for an event and confirm the hazard is removed or controlled. |
| Other | Exact location, asset, symptom and operating impact. | Assign the appropriate owner and confirm repair, mitigation or Management decision. |

### Classes

| Query Type | Capture and verify | Action through closure |
|---|---|---|
| Class timetable | Class, branch/studio, date and requested times. | Verify the current timetable and confirm the session or alternative. |
| Instructor query | Class, instructor, session date and requested information. | Confirm with the fitness/classes owner and relay the answer. |
| Class cancellation | Session, reason if known, affected booking and notice status. | Confirm cancellation, notify affected members and record alternatives or rebookings. |
| Class capacity | Session, booking/waitlist position and actual limit. | Confirm availability and waitlist/rebooking outcome. |
| Class complaint | Session, date, issue, impact and remedy sought. | Fitness owner checks and corrects the service issue. Cross-reference a formal Complaint if Management review is needed. |
| Sound/music issue | Studio, class, timing and equipment or volume issue. | Assign fitness/maintenance, verify correction at the affected class or test. |
| Class suggestion | Class/time requested and reason or demand. | Share with the schedule owner, record the decision and reply. |
| Beginner support | Desired class, experience, timing and support needed. | Confirm suitable class and instructor guidance, then record member’s booking or choice. |
| Other | Session, date, question and requested outcome. | Fitness team confirms answer or completes the named action. |

### Personal Training

| Query Type | Capture and verify | Action through closure |
|---|---|---|
| PT pricing | Package/session quantity, trainer if relevant and quote date. | PT confirms approved pricing, terms and next step. |
| PT booking | Trainer/service, session date and availability, booking reference. | PT confirms booking, change or alternative with the member. |
| PT complaint | Trainer/session, facts, date, impact and remedy. | PT lead investigates. Cross-reference a formal Complaint when Management needs to decide. Confirm the remedy. |
| PT refund | Package/session, payment reference and grounds. | PT verifies service delivery and opens or links **Refund → PT refund**. Give the member the refund reference and track through **3B**. |
| PT allocation | Goals, availability, trainer preference and assignment need. | PT assigns a trainer or records the alternative and confirms introduction/booking. |
| Programme request | Goal, experience, availability and safe limits relevant to the trainer. | PT reviews and arranges assessment or programme handoff. Confirm delivery or booked next step. |
| Other | PT service, date, trainer/reference and requested action. | PT owner resolves or records a tracked handoff and tells the member. |

### Complaint

| Query Type | Capture and verify | Action through closure |
|---|---|---|
| Staff attitude | Date, staff/shift, observed words or behaviour and witnesses. | Follow **3F** and record the investigated service outcome and member communication. |
| Poor assistance | Requested help, what was provided, gap and impact. | Follow **3F**, have the owner correct the service issue and confirm remedy. |
| Long waiting time | Arrival/contact time, time assisted, service and impact. | Follow **3F** and record findings and the response. |
| Incorrect information | Exact advice, source/date, correct policy or record and resulting impact. | Follow **3F** and confirm the correction and any approved remedy. |
| Manager request | Issue, previous attempts, urgency and preferred contact. | Send to Management through **3F**, record callback and decision. |
| General complaint | What happened, date/time/location, impact and requested remedy. | Follow **3F** through documented review and outcome. |
| Other | Specific grievance, evidence and requested result. | Follow **3F**. If an incident also occurred, track its safety response separately. |

### Incident

Take immediate safety action and alert the responsible manager or emergency responders as appropriate. Create the ticket once the immediate response is underway. Capture date/time, exact location, member or walk-in details, factual sequence, witnesses, immediate response, who was notified, and protected evidence references. Keep open until Management confirms the response, outstanding investigation or remediation is handed off with a reference, and any permitted member communication is recorded.

| Query Type | Capture and verify | Action through closure |
|---|---|---|
| Injury report | Activity, observed injury, time, witnesses and assistance given. | Follow site response procedures, notify Management, and document response and any follow-up. |
| Equipment accident | Equipment ID, activity, event, condition and response. | Remove unsafe equipment from service and track the maintenance inspection separately. Record Management outcome. |
| Slip/fall | Exact surface/location, conditions, signage and immediate assistance. | Secure the area, arrange clean-up/repair, and record the evidence and Management review. |
| Fight/altercation | Factual sequence, people involved, witnesses and response. | Get Management/security involved immediately, preserve evidence and record the agreed safety and contact outcome. |
| Theft/lost item | Item, last known location/time, owner and claim/evidence reference. | Check lost property and refer suspected theft for security/Management review. Confirm return or documented disposition. |
| Medical emergency | Time, location, observed facts, response called and handover. | Trigger immediate response, notify Management and record the response and subsequent authorised follow-up. |
| Safety complaint | Hazard or behaviour, place, time, risk and measures taken. | Secure the immediate risk, escalate and verify corrective action. Use a Facility ticket for a separately tracked repair. |
| Other | Nature of event, who was affected, timing, evidence and response. | Management verifies the response and records the final disposition and any linked work. |

**Separate Incident Log.** In **Fitness → Incident Log → Log Incident**, select **Member** or enter **Walk-in Name** and **Walk-in Contact**. Select **Incident Type** (Equipment Left on Floor, Improper Attire, Disrespectful Behaviour, Noise / Disturbance, Other), **Severity** (Low, Medium, High), enter the required **Description**, and choose **Logged By** where applicable. The log offers **Open, Warned, Resolved** and a WhatsApp shortcut when a number is available. When both records are needed, write the cross-reference in the query notes and update each record in its own screen. The Incident Log list has a status control. Keep detailed investigation and closure evidence on the ticket.

### General

| Query Type | Capture and verify | Action through closure |
|---|---|---|
| General information | Exact question, applicable branch/date and preferred reply. | Verify the answer and record what was communicated. |
| Operating hours | Branch, date, holiday or exception. | Confirm the published or manager-approved hours for that date and tell the person. |
| Guest pass | Visitor, visit date, eligibility and pass reference if issued. | Verify guest rules, record issue or decision, and confirm validity period. |
| Parking | Location, time, issue and any vehicle/reference details needed. | Confirm parking instructions or refer an incident/security matter and track the result. |
| Locker | Locker number/location, time and whether the issue concerns access, damage or property. | Arrange authorised assistance. Cross-reference maintenance or an Incident where appropriate and confirm the outcome. |
| Other | Specific question, what was checked and requested answer. | Answer directly or send to the team that can complete the named action. Record what was done and communicated. |

## Source and verification scope

Screen labels and workflow rules are based on the 1Cpace repository. The operational capture prompts and shared-queue handoffs are training guidance. Live provider execution, communication delivery and current staff configuration were outside this review.

- [Query categories, intake, updates and closure](onecpase/queries.py)
- [New Query screen](onecpase/templates/queries/add.html) and [Query Details screen](onecpase/templates/queries/detail.html)
- [ELEv8 routing](onecpase/query_routing.py)
- [Signed workflow rules](onecpase/query_forms.py), [staff workflow screen](onecpase/templates/queries/workflow.html), [member form](onecpase/templates/queries/member_form.html) and [settlement checks](onecpase/query_funnel.py)
- [PTP workflow](onecpase/ptp.py), [member screens](onecpase/templates/members/detail.html) and [arrears rules](onecpase/collections_engine.py)
- [DebiCheck entry screen](onecpase/templates/debicheck/add.html), [gate rules](onecpase/turnstile.py) and [Incident Log](onecpase/templates/fitness/incidents.html)
