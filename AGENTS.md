# Elysia AI source documentation rules

These rules apply to every human-authored source file in this repository.
They are mandatory for new code and for any source file touched by a change.

## Required documentation

- Every source module must begin with an accurate module/file description.
  - Python uses a module docstring.
  - TypeScript and JavaScript use a leading `/** ... */` file-purpose block (`@fileoverview` is recommended).
  - PowerShell scripts and modules use block-form comment-based help with a non-empty `.SYNOPSIS`; place it in the file prologue after only an optional BOM, shebang, or `#Requires` lines.
  - CSS uses a leading file-purpose comment; HTML places it immediately after the doctype.
- Every public class must have a class docstring or JSDoc block.
- Every public function and public method must have a docstring or JSDoc block that explains its contract, not merely repeats its name.
- Complex algorithms must explain how the approach works and why it was chosen.
- Non-obvious architecture, security, concurrency, persistence, compatibility, or rollback decisions must explain the design reason.
- Edge-case handling must explain why the edge case exists when that reason is not obvious from the code.
- `TODO` and `FIXME` comments are allowed only for concrete future work. State the missing behavior or defect and the condition for resolving it; do not leave vague reminders.

## Comments to avoid

Do not narrate ordinary assignments, `if` statements, loops, or direct calls line by line. Comments should preserve intent and reasoning that the code itself cannot express.

## Meaning of public

- In Python, a module/class/function/method name without a leading underscore is treated as public by the automated audit, including test entry points and test-double methods.
- In TypeScript and JavaScript, exported classes/functions and methods or function-valued properties on exported classes/interfaces are public. A separately exported, default-exported, or CommonJS-exported declaration counts the same as an inline `export`. A leading underscore does not make a TypeScript/JavaScript class member private; use the language's `private`, `protected`, or `#private` syntax.
- In PowerShell, functions and filters are public unless their name starts with `_` or uses the explicit `private:` scope. Public functions use adjacent block-form comment-based help with a non-empty `.SYNOPSIS`, either immediately before the definition or first inside its body.
- Private helpers still need reasoning comments when they implement a complex algorithm, security boundary, recovery rule, or non-obvious edge case.

## Scope and generated files

The rules cover Python (`.py`, `.pyi`, and `.pyw`), TypeScript, TSX, JavaScript-family files including JSX, PowerShell scripts/modules, CSS, and HTML/HTM. Markdown files are already documentation. JSON, lockfiles, schemas, binary assets, vendored dependencies, generated output, caches, and runtime user data are exempt because their formats either reject comments or are not maintained source.

## Required checks

Run these commands before completing a change:

```text
python scripts/check_python_documentation.py
cd desktop
npm run docs:check
```

The automated checks enforce structural coverage. Reviewers must still verify the accuracy and usefulness of explanations for algorithms, design decisions, edge cases, and future work.
