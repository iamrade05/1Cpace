# Reception query queue and notification plan

Agreed 24 October 2026. Planning only; application functionality has not been changed.

## Purpose

Reception needs an actionable work queue showing what is missing or unfinished on their side, what has been returned to them, what they await from members and what is approaching its deadline. They must also see management dependencies so they can follow up without taking over management decisions.

Do not substitute a generic all-queries listing for this queue. A broader view can follow existing permissions; gym-wide versus individually assigned reception visibility still needs configuration.

## Queue groups

| Group | Show | Reception action |
| --- | --- | --- |
| Needs reception action | New assigned requests, returned signed forms, documents awaiting verification, incomplete reception steps | Open and complete the required step. |
| Awaiting member | Form not submitted, missing signature/documents, replacement evidence requested, unanswered clarification | Follow up with the member using the existing query. |
| Awaiting management | Verified requests sent for a management decision, pending responses to reception follow-ups | Follow up with the responsible manager; approval permissions remain unchanged. |
| Due soon | Requests with approaching recorded due/follow-up dates | Prioritise the next action and contact the appropriate person. |
| Overdue | Unfinished steps past their recorded date | Follow up and record action/next date. |

Show later processing dependencies, such as an approved refund awaiting payment, when reception needs visibility to follow up. Label the actual responsible person; do not make reception appear responsible for executing another person's task.

## Each queue item

- Member name and query reference/type.
- Current workflow stage and current responsible person/team.
- Exact missing item or unfinished step, rather than only an unexplained status badge.
- Who reception is waiting for: member, manager or processor.
- Due/follow-up date and last relevant update.
- Direct link to the existing query, signed form, attachments and timeline.
- Clear next action such as Review submission, Contact member or Follow up with manager.

The query remains the source of truth; queue items are views of its work and dependencies, not duplicate cases.

Management must have an end-to-end overview of the same records, including application start, current holder, individual reception-check completion, management handoff and open/closed state. See `MANAGEMENT_QUERY_OVERSIGHT_PLAN.md`. Keep case ownership distinct from who currently needs to act.

## Notifications and follow-ups

Notify the appropriate reception staff when work is assigned/returned, a member submits or replaces evidence, management responds, or a recorded task approaches/becomes overdue. Keep pending dependencies visible even after a notification is read.

Use existing query follow-up dates and staff notification infrastructure where suitable. Avoid duplicate notifications for the same event. Reading/acknowledging a notification must not complete the business task. Clear or resolve outdated alerts when the actual task is completed or its state changes.

Record member/management follow-ups against the same query with actor, timestamp, communication outcome and next follow-up date. Distinguish a generated message from one successfully sent or delivered. This plan does not send messages or create scheduled automations by itself.

## Rules to configure

- Define Due soon lead time; the user has not specified a number of days.
- Preserve the separate freeze 14-day application cutoff and the shared 5–10-day processing notice; do not treat them as interchangeable deadlines.
- Define shared processing-clock interpretation before generating deadlines from the notice.
- Define reception visibility/ownership for shared versus individually assigned work.
- Follow existing approval/escalation permissions; do not invent an automatic management escalation threshold or deadline.

## Implementation order and acceptance

1. Map each planned query workflow to reception tasks, member/management dependencies and completion signals.
2. Build the grouped queue and actionable item detail from existing query data plus the new workflow stages.
3. Extend existing notifications with returned-submission/missing-item and due-soon events where needed.
4. Add recorded member/manager follow-ups and direct links to signed forms/profile evidence.
5. Test permissions, tenant isolation, assignment changes, replacements, deadline changes and completed-task cleanup.

Acceptance requires that reception can identify what is missing, who must act and when to follow up without opening every query. A member reply brings the case back to reception's attention. Acknowledged notifications leave unresolved work visible. Completed work stops generating stale reminders. Management follow-up visibility does not grant approval rights, and all forms/attachments remain protected on the member profile and original query.
