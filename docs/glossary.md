# Glossary

Terms the API and the documents use, and the formulas behind the measures. Field names
are the ones in [api-contract.md](api-contract.md).

## Terms

| Term | Meaning |
|---|---|
| Product line | A separate book of business with its own database, for example `auto` or `consumer`. `PRODUCT_LINES` names them. `all` means every line together. |
| Portfolio | A group of debts bought or managed together, named by `debt_attributes.portfolio_label`. Reports slice by portfolio. Do not confuse it with `portfolio_id`, which only makes an agreement number unique. |
| Agreement | A repayment plan an agent arranges with a debtor. It has one or more installments. |
| Generated agreement | An agreement counted for value: approved, broken and auto-broken ones. An agreement generated in a window counts even if it breaks later, because breaking is a later outcome. The set is `GENERATED` in [configuration.md](configuration.md#status-sets). |
| Installment | One payment of an agreement. `installment_no` counts from 0. |
| First installment | Installment 0, the down payment. Many measures count only this row so that an agreement is one row. |
| Slip | A payment slip: the document a debtor pays an installment with. In this project one slip is one installment. |
| Paid in grace | A slip with `paid_amount > 0` and `paid_at` at most `GRACE_DAYS` days after `due_at`. |
| Grace period | The days after a due date during which a payment still counts as on time. |
| Broken agreement | An agreement recorded as broken by hand (`BROKEN`). |
| Auto-broken agreement | An agreement the system broke, for example after the grace period (`AUTO_BROKEN`). |
| Exception | An agreement outside the normal rules that waits for approval (`PENDING_EXCEPTION`). |
| Rejected | An agreement a supervisor or the creditor refused (`REJECTED`). |
| Outreach attempt | One logged contact try: a call, a message. |
| Answer | An attempt whose result is flagged `answered`. |
| CPC | Right-party contact: the answer came from the right person (`answered` and `right_party` both set). |
| Agent | A collector. Responses identify one by `agent_login` and `agent_name`. |
| Window | The dates a request covers: `date_from` to `date_to`, inclusive, or today. |
| Team filter | `team=` keeps agents whose login or name contains a piece of text. |
| Envelope | The `{ meta, data, errors }` body every data route returns. |
| Run id | An id for one request, returned in `X-Run-Id` and in `meta.run_id`. |
| Targets | Quarterly goals per portfolio, read from an uploaded PDF. |
| Effectiveness | Are issued slips being paid on time. |

## Measures

Counts are integers. Ratios named `*_pct` run from 0 to 100. A ratio whose denominator is
zero is `null`, except where the field description says it is 0.

| Field | Definition |
|---|---|
| `attempt_count` | Distinct debt owners contacted. Several attempts on one account in the window count once. The `agent-performance` route counts per agent, line and day instead, so an account contacted on two days counts twice. |
| `answer_count` | The same count, restricted to attempts whose result was an answer. |
| `cpc_count` | The same count, restricted to attempts whose result was an answer from the right party. |
| `cpc_rate_pct` | `cpc_count / answer_count * 100`. |
| `agreement_count` | Generated agreements, counted by agreement number. |
| `agreement_count_by_contract` | Generated agreements counted once per debt they cover: an agreement over three debts counts three. Used for one headline card only; never as a denominator. |
| `agreement_value` | Sum of every installment of the generated agreements. |
| `avg_ticket` | Average `agreement_value` per agreement. |
| `avg_installments` | Average number of installments in the plans. |
| `first_installment_value` | Sum of installment 0 of the generated agreements. |
| `first_installment_received` | Amount paid on those first installments. |
| `conversion_pct` | `agreement_count / cpc_count * 100`: agreements over right-party contacts. |
| `agreements_per_attempt_pct` | `agreement_count / attempt_count * 100`. |
| `slips_issued` | Slips already due in the window. |
| `slips_paid` | Of those, slips paid in grace. |
| `slip_effectiveness_pct` | `slips_paid / slips_issued * 100`. |
| `paid_per_cpc_pct` | `slips_paid / cpc_count * 100`. A separate measure from `conversion_pct`. |
| `exception_count`, `exception_value` | Exceptions and their total value. |
| `rejected_count`, `rejected_value` | Rejected agreements, counted per debt, and their value. |
| `avg_balance_ratio_pct` | Average of agreement value over the balance of the debts it covers. |
| `avg_debt_age_days` | Average days from the earliest covered debt's due date to the start of the window. |
| `hours_worked` | A proxy: for each day, the span between an agent's first and last generated agreement, summed. |
| `conversion_pct` (effectiveness routes) | `slips_paid_in_grace / slips_total * 100`. The name is shared with the productivity measure; the route and the model description say which one applies. |
| `effectiveness_pct` | `amount_received / amount_maturing * 100`. |
| `cash_effectiveness_pct` (benchmarks) | Per agent, first-installment cash received over first-installment value. |
| `break_rate_pct` | `slips_broken / slips_total * 100` in a break-curve band. |

## Names

The names this project uses, next to what other systems often call the same thing.

| Here | Often called |
|---|---|
| `attempt_count` | contacts made, dialings |
| `answer_count` | live answers |
| `cpc_count` | RPC, right-party contacts |
| `agreement_count` | deals, promises |
| `slips_issued`, `slips_paid` | invoices issued and paid, bills |
| `product_line` | business unit, book |
| `portfolio_label` | client portfolio, wallet |
