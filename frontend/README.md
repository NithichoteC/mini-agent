# frontend/

User interface: Vite + React + TypeScript + Tailwind + shadcn/ui, with assistant-ui for the chat,
CodeMirror 6 for notes, react-arborist for the file tree and sigma.js for the graph.
It talks only to `backend/` over HTTP and Server-Sent Events; it holds no agent logic and no credentials.

    npm --prefix frontend ci
    npm --prefix frontend run build    # -> frontend/dist, served by `python main.py serve`
    npm --prefix frontend run lint     # ESLint + @shadcn/lint
    npm --prefix frontend run dev      # Vite on :5173, /api proxied to :8000
