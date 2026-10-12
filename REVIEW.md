# REVIEW.md

## Bugs found and fixes made

| # | Where | What was wrong | How I tested it | How I fixed it |
|---|---|---|---|---|
| 1 | `create_employee` | The API response could fail because MongoDB's internal `_id` was included in the returned document. | Created a new employee using Swagger. | Removed `_id` before returning the response. Employee creation returned `201 Created`. |
| 2 | Timestamp validation | The API should reject timestamps given in seconds instead of milliseconds. | Sent a timestamp in seconds to the punch-in endpoint. | Added timestamp validation. The API returned `422`. |
| 3 | Pagination | Page numbers and page sizes must stay within the allowed limits. | Tested page `0` and page size `101`. | Added validation. Both invalid requests were rejected with `422`. |
| 4 | Punch-in concurrency | Two requests should not create duplicate attendance records for the same employee and date. | Sent two punch-in requests at the same time for one employee and date. | Used the unique attendance key and conflict handling. One request returned `201` and the other returned `409`. |
| 5 | Punch-out concurrency | Two simultaneous punch-out requests should not both update the same attendance record successfully. | Sent two punch-out requests at the same time. | Added conflict handling so only one request succeeded in the tested case. The responses were `200` and `409`. |
| 6 | Regularization history | Attendance corrections must be recorded in history without losing earlier entries. | Made two corrections to the same attendance record. | Appended a history entry for each successful correction. Both entries were preserved. |
| 7 | Concurrent regularization | Concurrent corrections must not silently overwrite each other's history. | Ran five tests with two simultaneous corrections per attendance record. | Used a MongoDB compare-and-set update and checked the final history. All five test runs passed the script's consistency checks. |
| 8 | Work hours and half-day calculation | Work hours and half-day status must follow the assignment's calculation rules. | Tested a shift ending at 13:59 and at 14:00 after a 09:30 punch-in. | Verified `4.48` hours gives `half_day: true`, while `4.50` hours gives `half_day: false`. |
| 9 | Late-minute calculation | The 10-minute grace period must be handled correctly. | Tested punch-in times at 09:40:00, 09:40:01 and 10:15:59 for a 09:30 shift. | Checked the results against the expected late-minute rules. |
| 10 | Overtime calculation | Overtime should only count when the employee works at least 30 minutes beyond shift end. | Tested punch-out at 18:59 and 19:00 for an 18:30 shift end. | Verified overtime was `0` minutes at 18:59 and `30` minutes at 19:00. |
| 11 | Overnight shift | Attendance calculations must work when a shift crosses midnight. | Tested an employee with a 22:00–06:00 shift. | Verified the tested record returned 8 work hours with no late minutes or overtime. |
| 12 | Monthly analytics | Monthly attendance totals and percentages must be calculated correctly. | Checked October 2026 analytics for `EMP9016`. | Verified the response showed 22 working days, 1 present day and `4.55%` attendance. |
| 13 | Department summary | Employees without attendance records must still be included in department headcount. | Created an employee in a new department without attendance records. | Verified headcount was `1`, present days were `0`, and average work hours were `null`. |
| 14 | Late leaderboard | Employees with equal late minutes must share the same rank, and the next rank must be skipped. | Tested employees with late-minute totals of 45, 45 and 30. | Verified the ranks were `1, 1, 3`. Also checked that `limit=1` retained both employees tied at rank 1. |
| 15 | Department trend | The trend must include every date in the requested range, including weekends and dates without attendance records. | Tested the Engineering trend from October 12 to October 18, 2026. | Verified all seven dates were returned, weekend attendance rates were `null`, and the moving averages were populated as expected. |
| 16 | Explain endpoint | The tested query plans should use indexes instead of collection scans. | Checked the five explain modes in Swagger. | Verified the responses showed `IXSCAN` and no visible `COLLSCAN` in the tested plans. |

## Things I checked that were not defects

- MongoDB is suitable for this assignment because the required data is stored in MongoDB.
- Attendance records are identified by `emp