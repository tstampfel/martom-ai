import { fireEvent, render, screen, within } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { config } from '../config'
import { copy, telHref } from '../copy'
import { AppShell } from './AppShell'

describe('AppShell header categories', () => {
  it.each(copy.header.nav)('"%s" starts a chat with that category', (category) => {
    const onStartChat = vi.fn<(text: string) => void>()
    render(<AppShell onStartChat={onStartChat}>content</AppShell>)
    const nav = screen.getByRole('navigation', { name: copy.header.navLabel })
    fireEvent.click(within(nav).getByRole('button', { name: category }))
    expect(onStartChat).toHaveBeenCalledExactlyOnceWith(category)
  })

  it('no longer links the categories to the OLX shop', () => {
    render(<AppShell onStartChat={() => {}}>content</AppShell>)
    const nav = screen.getByRole('navigation', { name: copy.header.navLabel })
    expect(within(nav).queryAllByRole('link')).toHaveLength(0)
  })
})

describe('AppShell call button', () => {
  it('calls the shop number from config', () => {
    render(<AppShell onStartChat={() => {}}>content</AppShell>)
    const call = screen.getByRole('link', { name: copy.header.call })
    expect(call).toHaveAttribute('href', telHref(config.shopPhone))
  })
})
