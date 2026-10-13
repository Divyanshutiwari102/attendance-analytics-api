# DECISIONS.md

1. **Indexes.** I made unique indexes on employee code and on (employee code, date), so duplicates fail safely. I also added (department, code), (date, code) and (code, punch time) for department queries, date lists and finding a latest punch. I skipped a status index: it has only five values.

2. **Punch-in race.** Both requests try to insert the same employee and day. The unique index lets only one succeed. The other gets a duplicate-key error and I return 409, so one request gets 201 and one gets 409.

3. **Ties.** I rank employees by late minutes, so ties share a rank, like 1, 2, 2, 4. I keep everyone ranked at or below the limit, so employees tied at the cutoff are all returned.

4. **Headcount.** The summary starts from the employees, not the logs. It keeps everyone who joined by month end, then looks up their logs. Employees with no logs stay, so they still count.

5. **100x data.** The leaderboard and summary read every log of the month, about 1.4 million records per request at 100x data. I would store monthly totals per employee, update them on punch-out or correction, and read those instead.