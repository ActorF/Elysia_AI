/** Language-independent string rules shared by the renderer and wire parser. */

export function codePointLength(value: string): number {
  let length = 0
  for (const character of value) {
    if (character !== '') {
      length += 1
    }
  }
  return length
}

function isProtocolBlankCodePoint(codePoint: number): boolean {
  return (
    (codePoint >= 0x0009 && codePoint <= 0x000D)
    || codePoint === 0x0020
    || codePoint === 0x0085
    || codePoint === 0x00A0
    || codePoint === 0x1680
    || (codePoint >= 0x2000 && codePoint <= 0x200A)
    || (codePoint >= 0x2028 && codePoint <= 0x2029)
    || codePoint === 0x202F
    || codePoint === 0x205F
    || codePoint === 0x3000
    || codePoint === 0xFEFF
  )
}

function isProtocolBlankCharacter(character: string): boolean {
  return isProtocolBlankCodePoint(character.codePointAt(0) ?? -1)
}

export function hasNonBlankCodePoint(value: string): boolean {
  for (const character of value) {
    if (!isProtocolBlankCharacter(character)) {
      return true
    }
  }
  return false
}

export function trimProtocolBlankCharacters(value: string): string {
  let start = 0
  for (const character of value) {
    if (!isProtocolBlankCharacter(character)) {
      break
    }
    start += character.length
  }

  let end = value.length
  while (end > start) {
    let previous = end - 1
    const codeUnit = value.charCodeAt(previous)
    if (
      codeUnit >= 0xDC00
      && codeUnit <= 0xDFFF
      && previous > start
    ) {
      const preceding = value.charCodeAt(previous - 1)
      if (preceding >= 0xD800 && preceding <= 0xDBFF) {
        previous -= 1
      }
    }
    const character = value.slice(previous, end)
    if (!isProtocolBlankCharacter(character)) {
      break
    }
    end = previous
  }

  return value.slice(start, end)
}
