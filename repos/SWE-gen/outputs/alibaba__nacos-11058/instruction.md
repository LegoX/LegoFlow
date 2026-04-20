## Bug: Batch Instance Registration Causes Incorrect IP Count in Metrics Monitor

When using batch instance registration in Nacos naming service, the IP count metric tracked by `MetricsMonitor` becomes incorrect under the following scenarios:

### Problem Description

In `AbstractClient.addServiceInstance()`, when a batch instance is registered for the first time, the method calls `MetricsMonitor.incrementIpCountWithBatchRegister(instancePublishInfo)` to add the batch size to the IP count. However, the current implementation only increments the count for the **first** registration — if the same service is registered again with a different batch size (e.g., batch of 3, then batch of 5), the count is not properly adjusted for the difference.

Furthermore, when the client deregisters batch instances, the count decreases by an incorrect amount because it doesn't account for the previously registered batch size.

This results in:
1. IP count being too high or too low after multiple batch registrations for the same service/client
2. IP count not returning to 0 after the client disconnects

### Root Cause

The `addServiceInstance` method uses `publishers.put(service, instancePublishInfo)` and only increments the count when the put returns `null` (i.e., the service wasn't previously registered). On subsequent registrations for the same service, the old value is replaced without adjusting the count for the difference between old and new batch sizes.

The `MetricsMonitor.incrementIpCountWithBatchRegister()` method needs to be updated to accept **both** the old (previous) `InstancePublishInfo` and the new `BatchInstancePublishInfo`, so it can compute the delta and adjust the count correctly.

### Expected Behavior

The `MetricsMonitor.incrementIpCountWithBatchRegister()` method should accept both the old and new publish info:

```java
MetricsMonitor.incrementIpCountWithBatchRegister(oldInstancePublishInfo, newBatchInstancePublishInfo);
```

- When registering a batch for the **first time** (old value is `null`): increment count by the new batch size
- When **re-registering** with a different batch size (old value exists): adjust count by `newSize - oldSize` (can be positive or negative)
- When **deregistering**: decrement count by the correct batch size

Example scenarios that must work correctly:
1. Register batch of 1 instance → IP count = 1
2. Re-register same service with batch of 3 instances → IP count = 3 (not 4)
3. Re-register same service with batch of 2 instances → IP count = 2 (not 5 or 3)
4. Deregister → IP count = 0

### Current Broken Behavior

- Step 1: Register batch of 1 → count = 1 ✓
- Step 2: Re-register same service with batch of 3 → count stays at 1 (wrong, should be 3)
- Step 3: Deregister → count decrements by 3 → count = -2 (wrong, should be 0)

The `MetricsMonitor` class needs a new or updated method signature:
```java
public static void incrementIpCountWithBatchRegister(InstancePublishInfo oldInstancePublishInfo, BatchInstancePublishInfo newInstancePublishInfo)
```

Where:
- If `oldInstancePublishInfo` is `null`, add `newInstancePublishInfo.getInstancePublishInfos().size()` to the count
- If `oldInstancePublishInfo` is a `BatchInstancePublishInfo`, subtract its size and add the new size (net delta)
- If `oldInstancePublishInfo` is a regular `InstancePublishInfo`, subtract 1 and add the new batch size