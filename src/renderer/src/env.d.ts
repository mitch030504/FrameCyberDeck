/// <reference types="vite/client" />

// Electron <webview> tag type declaration for JSX
declare namespace JSX {
  interface IntrinsicElements {
    webview: React.DetailedHTMLProps<
      React.HTMLAttributes<HTMLElement> & {
        src?: string
        partition?: string
        allowpopups?: string
        preload?: string
        httpreferrer?: string
        useragent?: string
        disablewebsecurity?: string
        nodeintegration?: string
      },
      HTMLElement
    >
  }
}
