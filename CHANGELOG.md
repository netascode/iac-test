# 0.2.3 (unreleased)

- Handle file errors gracefully
- Allow empty YAML files

# 0.2.2

- Do not merge YAML dictionary list items, where each list item has unique attributes with primitive values

# 0.2.1

- Fix issue with YAML attributes named `tag`
- Fix multiple instances of `include` and `exclude` CLI arguments

# 0.2.0

- Add support for ansible-vault encrypted values
- Add `!env` tag to read values from environment variables

# 0.1.5

- Fix bug related to nested template directories

# 0.1.4

- Upgrade to Robot Framework 6.x
- Add option to provide CLI arguments as environment variables

# 0.1.3

- Add xUnit output (`xunit.xml`)

# 0.1.2

- Add custom Jinja tests
- Add iterate_list_folder rendering directive

# 0.1.1

- Add CLI options to select test cases by tag
- Add Requests and JSONlibrary

# 0.1.0

- Initial release
