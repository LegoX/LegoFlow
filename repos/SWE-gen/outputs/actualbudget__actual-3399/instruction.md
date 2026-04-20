A regression bug is affecting the transaction filter rules in the budgeting application. When creating rules using the `is` or `matches` operators on the `imported_payee` field, the filter returns no results even when the value should match.

The issue manifests as follows:
1. When using the `matches` operator with a pattern like `.*Store.*` on `imported_payee`, no transactions are found even when there are matching payees
2. When using the `is` operator with the exact payee string (populated by the 'create rule' button), no match is found
3. However, the `contains` operator works correctly with the same value

For example, if a transaction has an imported payee of "Amazon Store", the following behaviors are observed:
- `contains` with value "Amazon Store" → correctly finds the transaction
- `is` with value "Amazon Store" → incorrectly returns no results
- `matches` with value ".*Store.*" → incorrectly returns no results
- `matches` with value ".*" → works (but only because it matches everything)

The expected behavior is that `is` should perform a case-insensitive exact match, and `matches` should perform a case-insensitive regex match on the `imported_payee` field, similar to how they work on other string fields like `payee` or `notes`.

The root cause appears to be that the `imported_payee` field type is not properly inheriting the case-insensitive matching logic that other string fields use. The solution involves ensuring that `imported_payee` is treated as a standard string type for the purposes of rule evaluation, particularly for the `is` and `matches` operators.