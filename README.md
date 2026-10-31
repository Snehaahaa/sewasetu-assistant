# Sewa Setu – AI-Powered Pension Assistance Platform

**Project Owner & Lead Developer: Sneha G**

Sewa Setu is an AI-assisted service channel for the Old Age Pension Portal. It enables citizens to interact with the pension application process through an AI assistant and provides block officers with tools to review and process applications.

The system uses two MCP (Model Context Protocol) servers — one for citizens and another for officers — connected to the existing portal and PostgreSQL database.

## Architecture

```text
Citizen AI App ── OAuth (OTP) ──► Citizen MCP Server ──┐
                                                       │
                                                Shared Business Logic
                                                       │
Officer AI App ── OAuth (Login) ► Officer MCP Server ──┤
                                                       │
                                                PostgreSQL Database
                                                       │
                                          Existing Sewa Setu Portal
```

## Key Features

### Citizen Assistant
- Retrieve pension scheme information.
- Check application status.
- Create and update application drafts.
- Upload age-proof documents.
- Preview and submit applications.
- Withdraw pending applications.

### Officer Assistant
- View application queue summaries.
- List and inspect applications.
- Access uploaded age-proof documents.
- Approve or reject applications with a mandatory reason.
- Identify cases with potential red flags.

## Project Structure

| Directory / File | Description |
|---|---|
| `app/agent/citizen.py` | Citizen-facing MCP server |
| `app/agent/officer.py` | Officer-facing MCP server |
| `app/agent/common.py` | Shared MCP transport and OAuth validation |
| `app/app.py` | Main portal and shared application business logic |
| `evals/` | Evaluation personas, test harness and results |
| `agent_tests/` | Protocol and business-logic tests |
| `seed/` | Database seed data |
| `docs/` | Design note and deployment documentation |
| `docker-compose.yml` | Container orchestration |
| `Caddyfile` | Reverse proxy configuration |

## Technology Stack

- **Backend:** Python
- **AI Integration:** Model Context Protocol (MCP)
- **Communication:** Streamable HTTP
- **Authentication:** OAuth 2.1
- **Database:** PostgreSQL
- **Deployment:** Docker, Docker Compose
- **Reverse Proxy:** Caddy

## Evaluation and Testing

The project includes:
- Citizen and officer workflow evaluations.
- Scripted evaluation scenarios.
- Adversarial protocol and business-logic tests.
- Official MCP SDK client conformance checks.
- An evaluation dashboard available through the portal.

## Core Design Principles

- Both MCP servers use the same underlying application and database.
- Application submission and officer decisions use shared business logic.
- Authentication and authorization are enforced for the respective user roles.
- Officer decisions require a reason.
- Existing portal workflows remain connected to the same system of record.

## My Contributions

As the project owner and lead developer, my contributions include:

- Understanding the problem statement and defining the application workflow.
- Designing the citizen and officer interaction flows.
- Working on the MCP server architecture and integration.
- Implementing and integrating application workflows.
- Testing citizen and officer use cases.
- Reviewing system behaviour, debugging and validating the implementation.
- Preparing project documentation and deployment configuration.

## AI Assistance Disclosure

AI tools, including Claude, were used to assist with portions of code generation and modification, debugging, and development support. The project was reviewed and integrated as part of the overall implementation.

## Documentation

- [Deployment Guide](docs/DEPLOY.md)
- [System Design Note](docs/DESIGN_NOTE.md)

## Running Locally

For the local development setup, refer to the project documentation.

```bash
devshim/pg_up.sh
devshim/run_local.sh
```

Run the test suite:

```bash
PYTHONPATH=evals python3 agent_tests/test_flows.py
```

## Repository

GitHub: [Snehaahaa/sewasetu-assistant](https://github.com/Snehaahaa/sewasetu-assistant)
