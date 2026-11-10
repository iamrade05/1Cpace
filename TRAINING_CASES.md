# 1Cpace staff training | Membership applications, queries and collections

Companion exercise for the 1Cpace Training Manual. Use the Sales, Collections, Promise to Pay, Queries and Practice chapters for the screen walkthroughs. This handout supplies the fictional inputs, decisions and evidence the trainer checks for two end-to-end exercises.

## Prepare the session

Use a training gym or local demo with provider actions disabled. Start before either fictional member has been captured. Use reception, Collections and manager training accounts for handoffs. Record the generated prospect, member, query and collection references as you go.

All names, contacts, IDs, amounts and evidence below are fictional. The identity values are deliberately invalid RSA test numbers for local capture only. Use the session date for “today” and move the sample dates forward if the exercise is repeated. Enter the listed amounts on the training payment records independently of the tariff selected for the member.

| Field | Case 1 | Case 2 |
|---|---|---|
| First name | Amara | Kabelo |
| Last name | Training-One | Training-Two |
| RSA test ID | 9605140000000 | 9408205000000 |
| Date of birth | 14 May 1996 | 20 August 1994 |
| Cell | 0600000101 | 0600000102 |
| Email | amara.training@example.test | kabelo.training@example.test |
| Prospect source | Walk-In / Enquiry | Walk-In / Enquiry |
| Prospect note | TRAINING 01. Asks about the monthly package and wants a tour. | TRAINING 02. Wants evening access and asks how debit failures are handled. |
| Marketing consent | Off | Off |
| Join date | Today | Today |
| Member package | Select an available training tariff | Select an available training tariff |
| Payment Type | Cash | Cash for member capture. Simulate the failed debit separately in Collections. |
| Street number and name | 10 Training Lane | 12 Training Lane |
| Suburb, city and postal code | Training Park, Johannesburg, 2000 | Training Park, Johannesburg, 2000 |
| Country | South Africa | South Africa |
| Occupation | Office administrator | Warehouse coordinator |
| Access level | Local | Local |
| Emergency contact | Tumi Training, sibling, 0600000201 | Lerato Training, sibling, 0600000202 |
| Fitness goal | Build general fitness | Improve strength |
| Previous training experience | No | Yes |
| Health screening (PAR-Q) | All answers No for this fictional person | All answers No for this fictional person |
| Consultant | Verify the assigned training consultant or select one for manual capture | Verify the assigned training consultant or select one for manual capture |

Use the tariff to populate the contract length and monthly instalment. Write both values on the exercise record before saving. If practising manual capture, record the reception handoff separately. If practising the linked prospect path, have the assigned consultant record the walk-in attendance and agreement to join before starting the application.

## Review the application after saving

1. Open the linked application and record the generated member and application references. Confirm that the member details, tariff and assigned consultant match the capture. Check the current member status and application status.
2. Open the contract and check the name, tariff and term. Walk through the signing options. For a document exercise, ask the trainer for a synthetic PDF marked **TRAINING ONLY** and upload it under the appropriate document type. A placeholder document is useful for practising upload and download. Verified evidence is required to clear the actual checklist.
3. Read the application’s missing requirements and give the next action an owner. In this exercise the trainer supplies the simulated outcomes below for discussion. To practise a successful submission and activation in the application, use sandbox-issued test identities, test documents and an approved test mandate. The cash capture setup in this handout is for member entry practice.

| Checkpoint | Case 1 scenario card | Case 2 scenario card | What the trainer checks |
|---|---|---|---|
| Contract and identity | Mock contract supplied, identity still awaiting verification | Mock contract still awaiting signature, identity still awaiting verification | Trainee names the missing evidence and checks the correct member reference. |
| Affordability | Trainer says two statement months were reviewed and the result would qualify | Trainer says only one statement month was supplied | Trainee explains why Case 2 needs another statement month and records the follow-up. |
| DebiCheck | Simulated mandate result is pending | Submission held while evidence is incomplete | Trainee distinguishes submission, approval and activation. Both cases stay unverified in this exercise. |
| Registration fee | No registration fee receipt supplied | No registration fee receipt supplied | Trainee keeps the practice EFTs below under General payment and checks registration fee evidence separately. |

**Application handover note:** “TRAINING. Application [reference] is awaiting [specific evidence or decision]. [Named owner] to check [next action] on [date]. Member status remains Unverified.”

## Case 1 | Capture, proof of payment and resolution

1. At reception use **Capture Prospect**. Save Amara as a walk-in. Find her by name or cell and explain how reception hands the prospect to a consultant. Open the prospect and use its conversion action if your role offers it. Otherwise use **Members → Add Member** for the manual capture exercise. Choose one route so there is only one member record.
2. Open the member profile and check the generated reference, package, contact details and onboarding status. Use **Edit** to add the fitness goal and save. Add a note reading “TRAINING 01. Member requested confirmation of a R450 EFT.” Upload a trainer-approved dummy PDF as **Proof of Payment** if available, or use the appropriate generic document type. Download it and check the filename.
3. From the profile create a query under **Payments & Arrears → Proof of payment**. Set medium priority, assign a training Collections owner and set the next working day for follow-up. Paste the description below. Save and record the query reference.
4. Compare the member, amount, date and reference with the mock receipt. Search Payment Records for the reference before recording it. On **Record Payment** select **General payment**, enter **R450.00** as the **Outstanding Balance (R)** and **R450.00** paid, select **EFT / Paid**, use today's date, and put **TRAIN-EFT-001** in the notes. Leave the Itensity push option off. Save once.
5. Reopen the profile and query. Add an outcome note with the payment record reference and resolve the query after the trainer confirms the match. In Payment Records and Collections Reports verify one R450 cash receipt and zero unsettled for the simulated R450 charge.

**Query description:** “TRAINING 01. Member says a R450 EFT was made today using TRAIN-EFT-001. Verify receipt against the payment records and confirm whether the simulated charge is settled.”

**Mock receipt:** TRAINING ONLY | Amara Training-One | EFT | R450.00 | Today | TRAIN-EFT-001 | Trainer confirms receipt for exercise.

## Case 2 | Failed debit, partial payment and promise to pay

1. Capture Kabelo at reception, record the contact preference, hand off to the training consultant and capture the member once. Kabelo returns to reception, so log the conversation as in person. Ask the trainee where to check a Do Not Contact instruction before any outbound follow-up.
2. Use **Record Payment → General payment** to enter a simulated failed debit. Use **R900.00** in **Outstanding Balance (R)**, **R0.00** paid, **DebiCheck / Failed**, today's date and the note **“TRAIN-FAIL-002. Simulated failed debit, insufficient funds.”** Leave the Itensity push option off. Open the collection case from the profile or Case Queue and confirm the R900 opening amount.
3. Create a profile query under **Collections / PTP → Split payment arrangement**. Set medium priority, assign a training Collections owner and set a follow-up on the promise date. Paste the description below. Open the query and inspect its account decision and history.
4. Record the confirmed R300 EFT as a separate **General payment** with **R0.00** in **Outstanding Balance (R)**, **R300.00** paid, **EFT / Paid**, today and **TRAIN-EFT-002** in the notes. The failed debit already recorded the R900 charge. Log the in-person contact in the case. Use **Create PTP** or the case arrangement form to record **R600.00** due seven days from today. Assign an owner and use the manager training account if approval is required.
5. Open **Collections Dashboard → Case Queue → Collections Call Queue** as available. Find the case, review owner, next action and follow-up date, then open it from the queue and use **Back to Call Queue Profile** where shown. Review the PTP status and matching Payment Records. Check the Exceptions, Reconciliation and Reports screens with the trainer. Ask what evidence would justify a reconciliation change or legal referral before taking any action.
6. Keep the query and PTP open for the R600 promise. The trainer can choose to rehearse either outcome. For a kept promise, use a separate confirmed R600 EFT with zero new billed amount and record its reference before closing. For a missed promise, record the missed outcome, keep the balance owing and assign the next action.

**Query description:** “TRAINING 02. A simulated R900 debit failed. Member brought proof of a confirmed R300 EFT and promises to pay the remaining R600 seven days from today. Record the arrangement and follow up on its due date.”

**Mock receipt:** TRAINING ONLY | Kabelo Training-Two | EFT | R300.00 | Today | TRAIN-EFT-002 | Trainer confirms receipt for exercise.

**Expected before the final payment:** R900 billed, R300 cash collected, R600 unsettled. A promise or an uploaded receipt alone adds R0 to cash collected.

## Member profile walkthrough for both cases

Use the member profile to explain where each item lives and what it changes. The trainer can switch roles or open a record at the required stage when a button is conditional.

| Profile area | What the trainee should demonstrate |
|---|---|
| Header and identity | Find the generated member reference and status. Review personal, contact, membership, payment, address, emergency and fitness details where shown. Use **Edit**, **Back** and the queue return link. Identify **Call** without dialing the dummy number. |
| Notes and activity | Add a short factual note. Locate the timeline entry after a save. Explain which actions should leave a history entry. |
| Documents | Select document type, upload and download a dummy file. Locate analysis and delete actions where offered. For a delete exercise use an expendable dummy file only. |
| Open what you need | Open **Contract**. Identify **Send Contract to Sign**, **Open DebiCheck Mandate**, **Create Replacement Mandate**, **Check DebiCheck**, **Check NuPay for TT3**, NuPay review and Itensity push or override where shown. Have the trainee explain the prerequisites and what each action would send or change. Use a provider sandbox or a trainer demonstration for these actions. |
| Payments and collections | Use **Record Payment**, open the collection case, review the payment history and identify where a successful receipt changes the balance. |
| PTP and access | Find **Create PTP**, **Log Contact**, **Upload Receipt**, **Access Control**, **Update PTP Status**, the approval decision and receipt verification controls where shown. Use Case 2 for contact, promise and receipt review. Explain why access changes need their own authorized decision. Preview **Copy Message** without sending it. |
| Queries | Use **+ New Query**, open the linked query, locate status, owner, due date and history, and use the member-filtered query list. Show a completed outcome in Case 1 and a future follow-up in Case 2. |

## Leave application practice

Leave belongs to the trainee’s **staff** account. Use **My HR → Request leave** or **Leave requests → Request & sign leave**, depending on the view. Use a training employee with a leave balance and a separate training approver.

| Exercise | Staff submission | Approver check |
|---|---|---|
| Case 1 | Annual leave for one working day in the following week. Reason “TRAINING 01. Planned personal day.” Check the dates, complete the form’s confirmation and signature, then submit. | Find the pending request, confirm dates and balance, approve in the training environment and verify the trainee sees the decision. |
| Case 2 | Annual leave for two consecutive working days the following week. Reason “TRAINING 02. Planned appointment and travel.” Confirm, sign and submit. | Review coverage. For the exercise, decline with a clear reason and ask the trainee to verify the outcome and rehearse submitting revised dates. |

## Trainer sign-off

| Check | Evidence to record |
|---|---|
| Reception and application | Two distinct prospect and member references, application references, correct tariff and consultant, and one outstanding evidence item with an owner and due date per case. |
| Member profile | Trainee locates the note, uploaded document, contract, payment history, application and linked query. Trainee explains which buttons contact a provider and which actions need approval. |
| Queries | Amara’s proof-of-payment query has a verified outcome and is resolved. Kabelo’s arrangement query remains assigned and open at the follow-up checkpoint. |
| Collections | Show the payment records and reconcile the expected amounts. Amara has R450 received and R0 unsettled. Kabelo has R300 received and R600 unsettled at the promise checkpoint. |
| Leave | Show both request references and their decisions in the staff view. |

Record the trainee’s name, training date, trainer’s name and any steps to repeat. Have the trainee hand over Case 2 in one sentence with the balance, promise date, owner and next action.
