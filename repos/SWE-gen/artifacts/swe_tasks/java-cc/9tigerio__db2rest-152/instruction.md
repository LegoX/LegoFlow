The bulk insert endpoint needs to support TSID (Time-Sorted ID) generation for primary key columns. Currently, TSID generation works for single record creation but fails for bulk insert operations.

When creating multiple records in a single bulk request, the system should automatically generate TSID values for designated primary key columns. The TSID generation must support two types:

1. **TSID Number (Long)** - For BIGINT columns: When a table has a BIGINT primary key column configured for TSID, bulk insert should generate and populate unique long integer IDs for each record.

2. **TSID String** - For VARCHAR columns: When a table has a VARCHAR primary key column configured for TSID, bulk insert should generate and populate unique string IDs for each record.

The bulk insert endpoint should handle the following scenarios:
- When tsidType matches the defined column type (e.g., tsidType='NUMBER' for BIGINT column) - should succeed with auto-generated IDs
- When tsidType doesn't match the defined column type - should return an appropriate error
- When tsidType is not provided - should use the default TSID type configured for that column

For example, a director table with director_id as BIGINT should receive auto-generated TSID long values, while a review table with review_id as VARCHAR(20) should receive auto-generated TSID string values during bulk insert operations.

The expected behavior is that bulk insert requests should return HTTP 201 Created with a response indicating the number of rows created, with each row having its primary key populated by the appropriate TSID value.