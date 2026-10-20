Reconcile the contact exports in `/app/task_file/input/*.csv` into
`/app/task_file/output/contacts.csv` using Python's CSV format. The inputs are
UTF-8 and can contain quoted commas and embedded newlines.

Use the normalized email as the deduplication key: strip surrounding whitespace
and use Unicode `casefold()`. Ignore rows with an empty normalized email or an
`updated_epoch` that is not a nonnegative decimal integer. For each email, keep
the row with the largest numeric `updated_epoch`. Break ties by the
lexicographically greatest source filename, then the greatest data row number
within that file (the first data row is 1). Select the winner before applying
the deletion rule: if its stripped, case-insensitive `status` is `deleted`,
omit that contact entirely.

Write the CSV header `email,name,updated_epoch`. Sort the remaining contacts by
normalized email. Output normalized emails and stripped names, preserve commas
and embedded newlines in names, and write canonical integer timestamps without
leading zeros. Do not modify or delete any input files. Create the output
directory if necessary. No network is needed.
