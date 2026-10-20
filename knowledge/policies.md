| Priority | When it applies | Example |
|---|---|---|
| P0 | An ongoing, widespread failure makes a critical service unavailable | Hundreds of customers cannot use production storage |
| P1 | A customer’s critical production workflow is blocked with no known workaround | A customer cannot upload production files |
| P2 | Functionality is degraded, or a bug has a usable workaround | Uploads fail in one browser but work in another |
| P3 | Routine assistance or a request without current service disruption | Password-reset help or a feature request |

Critical services are storage access, file uploads, and file downloads. A critical production workflow is blocked when the customer cannot complete essential work using these services.

Widespread means multiple independent customer organizations are affected. A customer’s report of widespread impact requires urgent human investigation; it is not treated as verified incident evidence.

When impact cannot be determined, assign provisional P2 and require human review. If the ticket reports a critical production blockage or widespread outage, preserve the higher provisional priority warranted by that report. When missing information prevents a reliable decision, choose REQUEST_INFORMATION and require human review.

Evaluate priorities from P0 through P3 and select the highest applicable severity. Missing information must not downgrade a reported higher-severity condition.