/**
 * @fileoverview Enforces repository-wide file and public-API documentation coverage.
 */

import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import ts from 'typescript'

const desktopRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const repositoryRoot = path.resolve(desktopRoot, '..')
const sourceExtensions = new Set([
  '.cjs',
  '.cts',
  '.js',
  '.jsx',
  '.mjs',
  '.mts',
  '.ts',
  '.tsx',
])
const excludedDirectories = new Set([
  '.cache',
  '.git',
  '.mypy_cache',
  '.pytest_cache',
  '.ruff_cache',
  '.venv',
  '__pycache__',
  'build',
  'coverage',
  'dist',
  'dist-electron',
  'node_modules',
  'out',
  'playwright-report',
  'temp',
  'test-results',
  'tmp',
  'workspace',
])

/** Return whether a directory contains generated, cached, or temporary content. */
function isExcludedDirectory(name) {
  return (
    excludedDirectories.has(name) ||
    name.startsWith('.test-tmp') ||
    name.startsWith('pytest-cache-files-')
  )
}

/** Return all maintained files whose extension is in the requested set. */
function collectFiles(directory, extensions) {
  const files = []
  for (const entry of fs.readdirSync(directory, { withFileTypes: true })) {
    if (entry.isDirectory() && isExcludedDirectory(entry.name)) continue
    const entryPath = path.join(directory, entry.name)
    if (entry.isDirectory()) files.push(...collectFiles(entryPath, extensions))
    else if (extensions.has(path.extname(entry.name).toLowerCase())) files.push(entryPath)
  }
  return files
}

/** Select the TypeScript parser mode that matches a file's extension. */
function scriptKindFor(filePath) {
  const extension = path.extname(filePath).toLowerCase()
  if (extension === '.tsx' || extension === '.jsx') return ts.ScriptKind.TSX
  if (extension === '.js' || extension === '.mjs' || extension === '.cjs') {
    return ts.ScriptKind.JS
  }
  return ts.ScriptKind.TS
}

/** Return a repository-relative path with stable separators for diagnostics. */
function relativePathFor(filePath) {
  return path.relative(repositoryRoot, filePath).replaceAll('\\', '/')
}

/** Return whether modifiers expose a declaration outside its module. */
function hasExportModifier(node) {
  return Boolean(
    node.modifiers?.some(
      (modifier) =>
        modifier.kind === ts.SyntaxKind.ExportKeyword ||
        modifier.kind === ts.SyntaxKind.DefaultKeyword,
    ),
  )
}

/** Reduce a JSDoc comment payload to text while preserving inline-link labels. */
function jsDocCommentText(comment) {
  if (typeof comment === 'string') return comment
  if (Array.isArray(comment)) return comment.map((part) => part.text ?? '').join('')
  return ''
}

/** Return whether a JSDoc block contains an actual description, not only tags. */
function jsDocHasDescription(doc) {
  return jsDocCommentText(doc.comment).trim().length > 0
}

/** Return whether a declaration has its own meaningful JSDoc block. */
function hasJsDoc(node, sourceFile, fileDocStart) {
  return Boolean(
    node.jsDoc?.some(
      (doc) => doc.getStart(sourceFile) !== fileDocStart && jsDocHasDescription(doc),
    ),
  )
}

/** Extract a meaningful leading file JSDoc and its source position. */
function findFileDoc(sourceText) {
  const match = /^\uFEFF?\s*(\/\*\*[\s\S]*?\*\/)/u.exec(sourceText)
  if (!match) return undefined
  const text = match[1]
  const body = text
    .replace(/^\/\*\*/u, '')
    .replace(/\*\/$/u, '')
    .split(/\r?\n/u)
    .map((line) => line.replace(/^\s*\*\s?/u, ''))
    .join('\n')
    .replace(/^\s*@fileoverview\b/iu, '')
    .trim()
  return {
    meaningful: body.length > 0,
    start: match.index + match[0].indexOf(text),
  }
}

/** Render a stable declaration name for diagnostics. */
function declarationName(node) {
  if (!node.name) return '<default>'
  if (ts.isIdentifier(node.name) || ts.isPrivateIdentifier(node.name)) {
    return node.name.text
  }
  return node.name.getText()
}

/** Return whether an expression is the CommonJS module.exports object. */
function isModuleExports(expression) {
  return (
    ts.isPropertyAccessExpression(expression) &&
    ts.isIdentifier(expression.expression) &&
    expression.expression.text === 'module' &&
    expression.name.text === 'exports'
  )
}

/** Return whether an assignment target exposes a CommonJS export. */
function isCommonJsExportTarget(expression) {
  if (isModuleExports(expression)) return true
  if (!ts.isPropertyAccessExpression(expression)) return false
  return (
    (ts.isIdentifier(expression.expression) && expression.expression.text === 'exports') ||
    isModuleExports(expression.expression)
  )
}

/** Return a top-level CommonJS export assignment, when present. */
function commonJsExportAssignment(statement) {
  if (!ts.isExpressionStatement(statement) || !ts.isBinaryExpression(statement.expression)) {
    return undefined
  }
  const assignment = statement.expression
  if (
    assignment.operatorToken.kind !== ts.SyntaxKind.EqualsToken ||
    !isCommonJsExportTarget(assignment.left)
  ) {
    return undefined
  }
  return assignment
}

/** Add locally declared callables referenced by a CommonJS export expression. */
function collectCommonJsExpressionNames(expression, names) {
  if (ts.isIdentifier(expression)) {
    names.add(expression.text)
    return
  }
  if (!ts.isObjectLiteralExpression(expression)) return
  for (const property of expression.properties) {
    if (ts.isShorthandPropertyAssignment(property)) names.add(property.name.text)
    else if (ts.isPropertyAssignment(property) && ts.isIdentifier(property.initializer)) {
      names.add(property.initializer.text)
    }
  }
}

/** Return local identifiers exposed by ES-module or CommonJS export statements. */
function collectExportedNames(sourceFile) {
  const names = new Set()
  for (const statement of sourceFile.statements) {
    if (ts.isExportAssignment(statement) && ts.isIdentifier(statement.expression)) {
      names.add(statement.expression.text)
    } else if (
      ts.isExportDeclaration(statement) &&
      statement.exportClause &&
      ts.isNamedExports(statement.exportClause)
    ) {
      for (const element of statement.exportClause.elements) {
        names.add((element.propertyName ?? element.name).text)
      }
    }

    const assignment = commonJsExportAssignment(statement)
    if (assignment) collectCommonJsExpressionNames(assignment.right, names)
  }
  return names
}

/** Return whether a class member is part of its ordinary public API. */
function isPublicClassMember(member) {
  if (!member.name || ts.isPrivateIdentifier(member.name)) return false
  return !member.modifiers?.some(
    (modifier) =>
      modifier.kind === ts.SyntaxKind.PrivateKeyword ||
      modifier.kind === ts.SyntaxKind.ProtectedKeyword,
  )
}

/** Return whether a property declaration exposes a callable value. */
function isCallableProperty(member) {
  if (!ts.isPropertyDeclaration(member)) return false
  const callableInitializer =
    member.initializer &&
    (ts.isArrowFunction(member.initializer) || ts.isFunctionExpression(member.initializer))
  return Boolean(callableInitializer || (member.type && ts.isFunctionTypeNode(member.type)))
}

/** Return whether a variable declaration holds or declares a callable value. */
function isCallableVariable(declaration) {
  const callableInitializer =
    declaration.initializer &&
    (ts.isArrowFunction(declaration.initializer) ||
      ts.isFunctionExpression(declaration.initializer))
  return Boolean(callableInitializer || (declaration.type && ts.isFunctionTypeNode(declaration.type)))
}

/** Add a line-addressable documentation failure for one declaration. */
function reportMissingDoc(problems, sourceFile, node, kind, name = declarationName(node)) {
  const line = sourceFile.getLineAndCharacterOfPosition(node.getStart(sourceFile)).line + 1
  problems.push(`${sourceFile.fileName}:${line}: public ${kind} ${name} lacks meaningful JSDoc`)
}

/** Check methods and callable properties exposed by an exported class. */
function checkPublicClassMembers(problems, sourceFile, classNode, fileDocStart) {
  for (const member of classNode.members) {
    const callable =
      ts.isMethodDeclaration(member) ||
      ts.isGetAccessorDeclaration(member) ||
      ts.isSetAccessorDeclaration(member) ||
      isCallableProperty(member)
    if (
      callable &&
      isPublicClassMember(member) &&
      !hasJsDoc(member, sourceFile, fileDocStart)
    ) {
      reportMissingDoc(problems, sourceFile, member, 'method')
    }
  }
}

/** Return whether an interface member is a callable part of its public contract. */
function isCallableInterfaceMember(member) {
  return (
    ts.isMethodSignature(member) ||
    ts.isCallSignatureDeclaration(member) ||
    ts.isConstructSignatureDeclaration(member) ||
    (ts.isPropertySignature(member) && member.type && ts.isFunctionTypeNode(member.type))
  )
}

/** Check callable members exposed by an exported interface. */
function checkPublicInterfaceMembers(problems, sourceFile, interfaceNode, fileDocStart) {
  for (const member of interfaceNode.members) {
    if (
      isCallableInterfaceMember(member) &&
      !hasJsDoc(member, sourceFile, fileDocStart)
    ) {
      reportMissingDoc(problems, sourceFile, member, 'interface callable')
    }
  }
}

/** Check callable members defined directly inside an exported object literal. */
function checkExportedObjectMembers(problems, sourceFile, objectNode, fileDocStart) {
  for (const property of objectNode.properties) {
    const callable =
      ts.isMethodDeclaration(property) ||
      ts.isGetAccessorDeclaration(property) ||
      ts.isSetAccessorDeclaration(property) ||
      (ts.isPropertyAssignment(property) &&
        (ts.isArrowFunction(property.initializer) ||
          ts.isFunctionExpression(property.initializer)))
    if (callable && !hasJsDoc(property, sourceFile, fileDocStart)) {
      reportMissingDoc(problems, sourceFile, property, 'object method')
    }
  }
}

/** Check a function, class, or object exposed directly by an export assignment. */
function checkExportedExpression(
  problems,
  sourceFile,
  expression,
  documentationNode,
  fileDocStart,
) {
  if (
    ts.isArrowFunction(expression) ||
    ts.isFunctionExpression(expression) ||
    ts.isClassExpression(expression)
  ) {
    if (!hasJsDoc(documentationNode, sourceFile, fileDocStart)) {
      const kind = ts.isClassExpression(expression) ? 'class' : 'function'
      reportMissingDoc(problems, sourceFile, documentationNode, kind, '<default>')
    }
    if (ts.isClassExpression(expression)) {
      checkPublicClassMembers(problems, sourceFile, expression, fileDocStart)
    }
  } else if (ts.isObjectLiteralExpression(expression)) {
    checkExportedObjectMembers(problems, sourceFile, expression, fileDocStart)
  }
}

/** Check one JavaScript-family source file's file and exported-API coverage. */
function checkSourceFile(filePath) {
  const sourceText = fs.readFileSync(filePath, 'utf8')
  const relativePath = relativePathFor(filePath)
  const sourceFile = ts.createSourceFile(
    relativePath,
    sourceText,
    ts.ScriptTarget.Latest,
    true,
    scriptKindFor(filePath),
  )
  const problems = []
  const fileDoc = findFileDoc(sourceText)
  if (!fileDoc?.meaningful) {
    problems.push(`${relativePath}:1: source file lacks a meaningful leading file-purpose JSDoc`)
  }
  const fileDocStart = fileDoc?.start

  const separatelyExportedNames = collectExportedNames(sourceFile)
  for (const statement of sourceFile.statements) {
    if (ts.isInterfaceDeclaration(statement)) {
      const isPublic =
        hasExportModifier(statement) || separatelyExportedNames.has(statement.name.text)
      if (isPublic) {
        checkPublicInterfaceMembers(problems, sourceFile, statement, fileDocStart)
      }
      continue
    }

    const namedDeclaration =
      ts.isFunctionDeclaration(statement) || ts.isClassDeclaration(statement)
    if (namedDeclaration) {
      const name = statement.name?.text
      const isPublic = hasExportModifier(statement) || (name && separatelyExportedNames.has(name))
      if (isPublic && !hasJsDoc(statement, sourceFile, fileDocStart)) {
        const kind = ts.isClassDeclaration(statement) ? 'class' : 'function'
        reportMissingDoc(problems, sourceFile, statement, kind)
      }
      if (isPublic && ts.isClassDeclaration(statement)) {
        checkPublicClassMembers(problems, sourceFile, statement, fileDocStart)
      }
      continue
    }

    if (ts.isVariableStatement(statement)) {
      const exportedStatement = hasExportModifier(statement)
      const publicCallables = statement.declarationList.declarations.filter((declaration) => {
        if (!ts.isIdentifier(declaration.name)) return false
        const exported = exportedStatement || separatelyExportedNames.has(declaration.name.text)
        return exported && isCallableVariable(declaration)
      })
      if (publicCallables.length > 0 && !hasJsDoc(statement, sourceFile, fileDocStart)) {
        for (const declaration of publicCallables) {
          reportMissingDoc(problems, sourceFile, declaration, 'function')
        }
      }
      continue
    }

    if (ts.isExportAssignment(statement)) {
      checkExportedExpression(
        problems,
        sourceFile,
        statement.expression,
        statement,
        fileDocStart,
      )
      continue
    }

    const assignment = commonJsExportAssignment(statement)
    if (assignment) {
      checkExportedExpression(problems, sourceFile, assignment.right, statement, fileDocStart)
    }
  }

  return problems
}

/** Return whether a PowerShell help block has a non-empty synopsis description. */
function hasMeaningfulSynopsis(helpText) {
  const synopsis = /\.SYNOPSIS\b([\s\S]*?)(?=\r?\n\s*\.[A-Z][A-Z0-9]*\b|#>)/iu.exec(helpText)
  return Boolean(synopsis?.[1].replace(/^\s*#?\s?/gmu, '').trim())
}

/** Extract comment-based help from the permitted script prologue. */
function findPowerShellFileHelp(sourceText) {
  const match = /^\uFEFF?(?:(?:[ \t]*(?:#![^\r\n]*|#requires[^\r\n]*))?[ \t]*(?:\r?\n|$))*[ \t]*(<#[\s\S]*?#>)/iu.exec(
    sourceText,
  )
  if (!match) return undefined
  return {
    start: match.index + match[0].indexOf(match[1]),
    text: match[1],
  }
}

/** Return whether a help block immediately documents a function definition. */
function hasAdjacentPowerShellHelp(sourceText, definitionStart, bodyStart, fileHelpStart) {
  const prefix = sourceText.slice(0, definitionStart)
  const precedingMatches = [...prefix.matchAll(/<#[\s\S]*?#>/gu)]
  const precedingMatch = precedingMatches.at(-1)
  if (
    precedingMatch &&
    prefix.slice((precedingMatch.index ?? 0) + precedingMatch[0].length).trim() === ''
  ) {
    const precedingHelpStart = precedingMatch.index ?? 0
    if (
      precedingHelpStart !== fileHelpStart &&
      hasMeaningfulSynopsis(precedingMatch[0])
    ) {
      return true
    }
  }

  const body = sourceText.slice(bodyStart)
  const bodyHelp = /^\s*(<#[\s\S]*?#>)/u.exec(body)?.[1]
  return Boolean(bodyHelp && hasMeaningfulSynopsis(bodyHelp))
}

/** Verify PowerShell script/module and public-function comment-based help. */
function checkPowerShellFile(filePath) {
  const sourceText = fs.readFileSync(filePath, 'utf8')
  const relativePath = relativePathFor(filePath)
  const problems = []
  const fileHelp = findPowerShellFileHelp(sourceText)
  if (!fileHelp || !hasMeaningfulSynopsis(fileHelp.text)) {
    problems.push(
      `${relativePath}:1: PowerShell source lacks meaningful prologue help with .SYNOPSIS`,
    )
  }

  const functionPattern =
    /^[ \t]*(?:function|filter)[ \t]+(?:(global|script|local|private):)?([A-Z_][\w-]*)(?:[ \t]*\([^{}]*\))?[ \t]*(?:\r?\n[ \t]*)?\{/gimu
  for (const match of sourceText.matchAll(functionPattern)) {
    const scope = match[1]?.toLowerCase()
    const name = match[2]
    if (scope === 'private' || name.startsWith('_')) continue
    const bodyStart = (match.index ?? 0) + match[0].length
    if (
      !hasAdjacentPowerShellHelp(
        sourceText,
        match.index ?? 0,
        bodyStart,
        fileHelp?.start,
      )
    ) {
      const line = sourceText.slice(0, match.index).split(/\r?\n/u).length
      problems.push(
        `${relativePath}:${line}: public PowerShell function ${name} lacks adjacent meaningful comment-based help`,
      )
    }
  }
  return problems
}

/** Remove block-comment framing so empty CSS/HTML comments cannot satisfy the rule. */
function blockCommentBody(comment, opening, closing) {
  return comment.slice(opening.length, -closing.length).trim()
}

/** Verify a maintained CSS or HTML file has a meaningful file-purpose comment. */
function checkStyleOrMarkupFile(filePath) {
  const sourceText = fs.readFileSync(filePath, 'utf8').replace(/^\uFEFF/u, '')
  const relativePath = relativePathFor(filePath)
  if (path.extname(filePath).toLowerCase() === '.css') {
    const match = /^\s*(\/\*[\s\S]*?\*\/)/u.exec(sourceText)
    return match && blockCommentBody(match[1], '/*', '*/')
      ? []
      : [`${relativePath}:1: CSS source lacks a meaningful leading file-purpose comment`]
  }
  const match = /^\s*<!doctype html>\s*(<!--[\s\S]*?-->)/iu.exec(sourceText)
  return match && blockCommentBody(match[1], '<!--', '-->')
    ? []
    : [`${relativePath}:1: HTML source lacks a meaningful file-purpose comment after its doctype`]
}

/** Run the documentation audit and expose a CI-friendly exit status. */
function main() {
  const sourceFiles = collectFiles(repositoryRoot, sourceExtensions).sort()
  const powerShellFiles = collectFiles(repositoryRoot, new Set(['.ps1', '.psm1'])).sort()
  const styleAndMarkupFiles = collectFiles(
    repositoryRoot,
    new Set(['.css', '.htm', '.html']),
  ).sort()
  const problems = [
    ...sourceFiles.flatMap(checkSourceFile),
    ...powerShellFiles.flatMap(checkPowerShellFile),
    ...styleAndMarkupFiles.flatMap(checkStyleOrMarkupFile),
  ]

  if (problems.length > 0) {
    for (const problem of problems) console.error(problem)
    console.error(`Source documentation check failed: ${problems.length} problem(s).`)
    process.exitCode = 1
    return
  }

  console.log(
    `Source documentation check passed: ${sourceFiles.length + powerShellFiles.length + styleAndMarkupFiles.length} file(s).`,
  )
}

main()
