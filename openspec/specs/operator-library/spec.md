# operator-library Specification

## Purpose
What the operator library shows about each published story, and how its actions find a story's family, so the operator can moderate without ever seeing a family token.

## Requirements

### Requirement: The library shows the family hash, not the token

For a family's private story, the operator library SHALL show the family's hash (the same short identifier the logs use) and SHALL NOT contain the family token anywhere in the page, including form values and attributes.

#### Scenario: A private story in the library
- **WHEN** the operator opens the library and a family's private story is listed
- **THEN** the page shows that family's hash and contains no family token

#### Scenario: A private story's cover
- **WHEN** a family's private story with a cover is listed
- **THEN** its cover loads from an operator-only address that contains no family token

#### Scenario: A shared-shelf story
- **WHEN** a shared-shelf story is listed
- **THEN** it is shown as shared, with no family identifier

### Requirement: Library actions find the family server-side

Deleting or hiding a story from the library SHALL identify the story by its id, and the server SHALL look up its family. The action SHALL NOT take a family token from the client.

#### Scenario: Delete a family's private story
- **WHEN** the operator deletes a family's private story from the library
- **THEN** that story is removed from that family's overlay, and no other family's content changes

#### Scenario: A posted token is ignored
- **WHEN** a library action request carries a family token field
- **THEN** the field is ignored and the server uses the family it looked up for the story id
