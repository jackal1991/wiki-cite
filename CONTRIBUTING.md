# Contributing to Wikipedia Citation & Cleanup Tool

Thank you for your interest in contributing! This document provides guidelines for contributing to the project.

## Code of Conduct

- Be respectful and inclusive
- Follow Wikipedia's policies and guidelines
- Ensure all contributions maintain the tool's safety guardrails
- Never submit code that could harm Wikipedia or its editors

## Getting Started

1. Fork the repository
2. Clone your fork: `git clone https://github.com/yourusername/wiki-cite.git`
3. Create a virtual environment: `python -m venv venv`
4. Activate it: `source venv/bin/activate` (or `venv\Scripts\activate` on Windows)
5. Install dependencies: `pip install -r requirements.txt`
6. Install dev dependencies: `pip install -e ".[dev]"`

## Development Workflow

1. Create a new branch: `git checkout -b feature/your-feature-name`
2. Make your changes
3. Add tests for new functionality
4. Run tests: `pytest tests/`
5. Run type checking: `mypy wiki_cite/`
6. Commit your changes with clear messages
7. Push to your fork
8. Create a pull request

## Coding Standards

### Python Style

- Follow PEP 8
- Use type hints for all functions
- Maximum line length: 100 characters
- Use meaningful variable names

### Documentation

- Add docstrings to all functions and classes
- Update README.md if adding new features
- Include examples in docstrings where helpful

### Testing

- Write tests for all new functionality
- Aim for >80% code coverage
- Use pytest fixtures for common setup
- Mock external API calls

Example test:

```python
def test_validate_minimal_edit(guardrails):
    """Test that minimal edits are accepted."""
    edit = ProposedEdit(
        edit_type=EditType.GRAMMAR_FIX,
        original_text="The cat are sleeping",
        proposed_text="The cat is sleeping",
        rationale="Subject-verb agreement",
        confidence="high"
    )

    is_valid, reason = guardrails.validate_edit(edit, "", "")
    assert is_valid
```

## Areas for Contribution

### High Priority

- **Source Finding**: Improve source search algorithms
- **Guardrails**: Add more policy violation checks
- **Testing**: Increase test coverage
- **Documentation**: Improve user guides

### Medium Priority

- **Performance**: Optimize article analysis speed
- **UI/UX**: Improve web interface design
- **Configuration**: Add more customization options
- **Monitoring**: Add logging and metrics

### Low Priority

- **Additional APIs**: Support more source databases
- **Export**: Add ability to export edit reports
- **Statistics**: Track tool effectiveness over time

## Guardrails Development

When modifying guardrails:

1. **Never relax safety constraints** without discussion
2. Test with real Wikipedia articles
3. Ensure edits remain truly minimal
4. Document policy reasoning

## Pull Request Guidelines

### Before Submitting

- [ ] All tests pass
- [ ] Type checking passes
- [ ] Code follows style guidelines
- [ ] Documentation is updated
- [ ] Commit messages are clear

### PR Description Should Include

- What changes were made and why
- How to test the changes
- Any breaking changes
- Related issues (if applicable)

### Review Process

1. Maintainers will review within 1 week
2. Address any requested changes
3. Once approved, maintainers will merge

## Testing with Wikipedia

### Important Rules

1. **Never test on production Wikipedia** without approval
2. Use [test.wikipedia.org](https://test.wikipedia.org) for testing
3. Always use the `(AI-assisted, human-reviewed)` edit summary
4. Follow Wikipedia's bot approval process

### Test Wikipedia Setup

```python
# Use test Wikipedia
site = mwclient.Site("test.wikipedia.org")
site.login("YourTestUsername", "YourTestPassword")
```

## Reporting Issues

When reporting issues, include:

- Python version
- Operating system
- Steps to reproduce
- Expected vs. actual behavior
- Relevant log output
- Article title (if applicable)

Use this template:

```markdown
## Description
[Clear description of the issue]

## Steps to Reproduce
1.
2.
3.

## Expected Behavior
[What should happen]

## Actual Behavior
[What actually happens]

## Environment
- Python version:
- OS:
- Package version:

## Additional Context
[Any other relevant information]
```

## Feature Requests

We welcome feature requests! Please:

1. Check existing issues first
2. Describe the use case
3. Explain why it's needed
4. Consider Wikipedia policy implications

## Code Review Checklist

Reviewers should check:

- [ ] Code follows style guidelines
- [ ] Tests are comprehensive
- [ ] Documentation is clear
- [ ] Guardrails are not weakened
- [ ] Performance impact is acceptable
- [ ] Security implications considered
- [ ] Wikipedia policy compliance

## License

By contributing, you agree that your contributions will be licensed under the MIT License.

## Questions?

- Open an issue for questions
- Join discussions in GitHub Discussions
- Email: [maintainer email]

Thank you for contributing!
