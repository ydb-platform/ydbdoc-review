---
title: Document
---
# Configuration {#setup}

The first sentence has **emphasis** and a [link](guide.md).
The second contains `code`.

- First item.

  Item continuation.
  - Nested item.

> First quote.
>
> Second paragraph of the quote.

| Name | Value |
|---|---|
| Key | Value |

#|
|| Name | Description ||
|| Row | Text | Extra cell ||
|#

{% note info "Important" %}
Note body.
{% endnote %}

{% list tabs %}
- First tab

  Tab body.
{% endlist %}

{% cut "Details" %}
Section body.
{% endcut %}

{% if enabled %}
Main branch.
{% else %}
Fallback branch.
{% endif %}

[*term]: Term definition.

```python
# Comment
print("Message")
```

```mermaid
graph LR
A[Start] --> B[End]
```

![Description](image.png "Title")
