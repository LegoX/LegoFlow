When actors use `request()` with a timeout and the request completes successfully (or is canceled/disposed), the timeout action continues to hold onto actor references until the timeout expires. This prevents the actor system from shutting down in a timely manner.

For example, consider two chained actors where the parent calls a child with a 2-second timeout:

```cpp
self->request(*child, seconds(2), ping_atom::value).then([=](int i) mutable {
  rp.deliver(i);
}, [=](error& e) {
  // handle error
});
```

Even when the request completes immediately and the response is delivered, the program takes the full 2 seconds to exit. Setting the timeout to `infinite` causes the program to exit immediately (because no timed action is scheduled), while any finite timeout delays shutdown by exactly that duration.

The root cause is in how actions (particularly timed actions used for request timeouts) manage their function object lifetime. When an action is disposed (i.e., marked as canceled/done), the state enum is updated to reflect disposal, but the function object stored inside the action is **not released**. Since the function object (a lambda) can capture strong actor pointers or other references via its closure, those references remain alive until all intrusive pointers to the action itself go out of scope — which only happens when the timer fires.

The fix should ensure that when an action is disposed, the stored function object is also destroyed immediately (e.g., by resetting or releasing the functor), so that any captured actor handles or other state it owns are released promptly rather than held until the timeout expires.

Expected behavior: After a `request()` with a finite timeout completes (successfully or with an error), disposing the timeout action should immediately release the function object and any captured references, allowing the actor system to shut down without waiting for the timeout to expire.

Actual behavior: The function object (and its captured actor references) is retained until the action's destructor runs, which only happens when the timer fires at the original timeout deadline.