import * as React from "react";

const TOAST_LIMIT = 5;
const TOAST_REMOVE_DELAY = 5000;

type ToastVariant = "default" | "success" | "warning" | "destructive";

type Toast = {
  id: string;
  title?: string;
  description?: string;
  variant?: ToastVariant;
  open?: boolean;
  onOpenChange?: (open: boolean) => void;
};

let count = 0;
function genId() {
  count = (count + 1) % Number.MAX_SAFE_INTEGER;
  return count.toString();
}

type Action =
  | { type: "ADD_TOAST"; toast: Toast }
  | { type: "UPDATE_TOAST"; toast: Partial<Toast> & { id: string } }
  | { type: "DISMISS_TOAST"; toastId: string }
  | { type: "REMOVE_TOAST"; toastId: string };

interface State {
  toasts: Toast[];
}

const toastTimeouts = new Map<string, ReturnType<typeof setTimeout>>();

function addToRemoveQueue(toastId: string) {
  if (toastTimeouts.has(toastId)) return;
  const timeout = setTimeout(() => {
    toastTimeouts.delete(toastId);
    dispatch({ type: "REMOVE_TOAST", toastId });
  }, TOAST_REMOVE_DELAY);
  toastTimeouts.set(toastId, timeout);
}

function reducer(state: State, action: Action): State {
  switch (action.type) {
    case "ADD_TOAST":
      return { ...state, toasts: [action.toast, ...state.toasts].slice(0, TOAST_LIMIT) };
    case "UPDATE_TOAST":
      return { ...state, toasts: state.toasts.map((t) => (t.id === action.toast.id ? { ...t, ...action.toast } : t)) };
    case "DISMISS_TOAST":
      addToRemoveQueue(action.toastId);
      return { ...state, toasts: state.toasts.map((t) => (t.id === action.toastId ? { ...t, open: false } : t)) };
    case "REMOVE_TOAST":
      return { ...state, toasts: state.toasts.filter((t) => t.id !== action.toastId) };
  }
}

const listeners: Array<(state: State) => void> = [];
let memoryState: State = { toasts: [] };

function dispatch(action: Action) {
  memoryState = reducer(memoryState, action);
  listeners.forEach((l) => l(memoryState));
}

interface ToastInput {
  title?: string;
  description?: string;
  variant?: ToastVariant;
}

function toast({ title, description, variant = "default" }: ToastInput) {
  const id = genId();
  dispatch({
    type: "ADD_TOAST",
    toast: { id, title, description, variant, open: true, onOpenChange: (open) => { if (!open) dispatch({ type: "DISMISS_TOAST", toastId: id }); } },
  });
  addToRemoveQueue(id);
  return id;
}

function useToast() {
  const [state, setState] = React.useState<State>(memoryState);
  React.useEffect(() => {
    listeners.push(setState);
    return () => {
      const i = listeners.indexOf(setState);
      if (i > -1) listeners.splice(i, 1);
    };
  }, []);
  return { ...state, toast, dismiss: (id: string) => dispatch({ type: "DISMISS_TOAST", toastId: id }) };
}

export { useToast, toast };
