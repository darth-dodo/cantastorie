# Spec Delta

## Purpose

How a parent account gets its family on first sign-in, so that an account can only ever be attached to a family it started, with a limit that keeps provisioning from being called at volume.

## ADDED Requirements

### Requirement: First sign-in starts a new family

Provisioning SHALL create a new family for a signed-in account that has none. It SHALL NOT accept a family token from the client, and SHALL NOT attach an account to an existing family.

#### Scenario: Start a new family
- **WHEN** a signed-in account with no family provisions
- **THEN** a new family is created for it and the account is attached to it

#### Scenario: A family token is not accepted
- **WHEN** provisioning is called with a family token in the request
- **THEN** it is refused and no account is attached to any family

#### Scenario: An account that already has a family
- **WHEN** an account that already has a family provisions again
- **THEN** its family is unchanged

### Requirement: Provisioning is rate-limited

Provisioning SHALL be limited per account. Over the limit, the request SHALL be refused with a "try again later" answer and change nothing.

#### Scenario: Too many attempts
- **WHEN** an account makes more than 5 provisioning attempts within 15 minutes
- **THEN** further attempts in that window are refused and change nothing

### Requirement: Provisioning logs never carry the token

Logs written while provisioning SHALL identify a family by its hash, never by its token.

#### Scenario: A family is created
- **WHEN** provisioning creates a family
- **THEN** the log line names the family hash and contains no token
