```mermaid
sequenceDiagram
    actor user as User
    participant node as YDB node
    participant cache as Node cache
    participant auth as Authentication subsystem

    user->>node: Request with the same data
    node->>cache: Find the entry
    cache-->>node: Entry found
    Note right of cache: The life_time countdown restarts

    opt Refresh time has arrived
        node->>auth: Revalidate the authentication token
        alt Validation succeeded
            auth-->>node: Validation result
            node->>node: Create a new user token
            node->>cache: Update the entry
        else Retryable error
            auth-->>node: Error
            Note right of node: Schedule a retry
        else Permanent error
            auth-->>node: Error
            node->>cache: Stop using the user token
        end
    end

    alt Entry was unused for life_time or has expired
        node->>cache: Delete the entry
    end
```
