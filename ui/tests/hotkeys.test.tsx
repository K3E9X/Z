import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { navKey, ownsNavigationKeys, useHotkeys } from '@/lib/hotkeys'

/** jsdom has no layout: give an element a scrollable box. */
function makeScrollable(el: HTMLElement) {
  el.style.overflowY = 'auto'
  Object.defineProperty(el, 'scrollHeight', { configurable: true, value: 900 })
  Object.defineProperty(el, 'clientHeight', { configurable: true, value: 300 })
}

describe('ownsNavigationKeys', () => {
  it('is true on controls and fields and inside a pane that scrolls', () => {
    render(
      <div>
        <button type="button">Play</button>
        <input type="range" aria-label="Position" />
        <div data-testid="pane" tabIndex={0}>
          <p>Long detail</p>
        </div>
        <div data-testid="still" tabIndex={0} style={{ overflowY: 'auto' }} />
      </div>,
    )
    expect(ownsNavigationKeys(screen.getByRole('button'))).toBe(true)
    expect(ownsNavigationKeys(screen.getByLabelText('Position'))).toBe(true)
    expect(ownsNavigationKeys(document.body)).toBe(false)
    expect(ownsNavigationKeys(screen.getByTestId('still'))).toBe(false)
    makeScrollable(screen.getByTestId('pane'))
    expect(ownsNavigationKeys(screen.getByTestId('pane'))).toBe(true)
    expect(ownsNavigationKeys(screen.getByText('Long detail'))).toBe(true)
  })
})

function Player({ onToggle, onClick }: { onToggle: () => void; onClick: () => void }) {
  useHotkeys({ space: navKey(onToggle), end: navKey(onToggle) })
  return (
    <button type="button" onClick={onClick}>
      Speed 4x
    </button>
  )
}

describe('navKey', () => {
  it('leaves Space to a focused button and handles it when nothing is focused', async () => {
    const user = userEvent.setup()
    const toggle = vi.fn()
    const click = vi.fn()
    render(<Player onToggle={toggle} onClick={click} />)
    screen.getByRole('button').focus()
    await user.keyboard(' ')
    expect(click).toHaveBeenCalledTimes(1)
    expect(toggle).not.toHaveBeenCalled()
    screen.getByRole('button').blur()
    await user.keyboard(' ')
    await user.keyboard('{End}')
    expect(toggle).toHaveBeenCalledTimes(2)
    expect(click).toHaveBeenCalledTimes(1)
  })
})
