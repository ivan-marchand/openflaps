import { useState, useEffect, useRef } from 'preact/hooks'
import './SplitFlapDisplay.css'

function Tile({ char }) {
  const [flipping, setFlipping] = useState(false)
  const prevChar = useRef(char)

  useEffect(() => {
    if (char !== prevChar.current) {
      prevChar.current = char
      setFlipping(true)
      const t = setTimeout(() => setFlipping(false), 200)
      return () => clearTimeout(t)
    }
  }, [char])

  const display = char === ' ' ? '\u00A0' : char

  return (
    <div class={`sfd-tile${flipping ? ' flipping' : ''}`}>
      <div class="sfd-top" />
      <div class="sfd-seam" />
      <div class="sfd-bottom" />
      <span class="sfd-char">{display}</span>
    </div>
  )
}

export function SplitFlapDisplay({ text = '    ', moduleCount = 4, charPerRow = -1 }) {
  const chars = text.padEnd(moduleCount, ' ').slice(0, moduleCount).split('')
  const rowWidth = charPerRow > 0 ? charPerRow : chars.length
  const rows = []
  for (let i = 0; i < chars.length; i += rowWidth) {
    rows.push(chars.slice(i, i + rowWidth))
  }
  return (
    <div class="sfd-grid" style={{ '--cols': Math.max(1, Math.min(rowWidth, chars.length)) }}>
      {rows.map((row, r) => (
        <div class="sfd-row" key={r}>
          {row.map((c, i) => <Tile key={i} char={c} />)}
        </div>
      ))}
    </div>
  )
}
