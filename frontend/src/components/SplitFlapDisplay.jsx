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
      <div class="sfd-top"><span class="sfd-char">{display}</span></div>
      <div class="sfd-seam" />
      <div class="sfd-bottom"><span class="sfd-char">{display}</span></div>
    </div>
  )
}

export function SplitFlapDisplay({ text = '    ' }) {
  const chars = text.padEnd(4, ' ').slice(0, 4).split('')
  return (
    <div class="sfd-row">
      {chars.map((c, i) => <Tile key={i} char={c} />)}
    </div>
  )
}
