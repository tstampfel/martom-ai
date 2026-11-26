import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'

// Fonts are self-hosted from the @fontsource packages (SIL OFL 1.1), so no
// request goes to Google Fonts. Only the latin and latin-ext subsets (č, ć, đ,
// š, ž) and the weights the mockups use are bundled.
import '@fontsource/inter/latin-400.css'
import '@fontsource/inter/latin-ext-400.css'
import '@fontsource/inter/latin-500.css'
import '@fontsource/inter/latin-ext-500.css'
import '@fontsource/inter/latin-600.css'
import '@fontsource/inter/latin-ext-600.css'
import '@fontsource/inter/latin-700.css'
import '@fontsource/inter/latin-ext-700.css'
import '@fontsource/outfit/latin-700.css'
import '@fontsource/outfit/latin-ext-700.css'
import '@fontsource/outfit/latin-800.css'
import '@fontsource/outfit/latin-ext-800.css'

import './styles/tokens.css'
import './styles/global.css'
import App from './App.tsx'

const root = document.getElementById('root')
if (!root) {
  throw new Error('Missing #root element in index.html')
}

createRoot(root).render(
  <StrictMode>
    <App />
  </StrictMode>,
)
