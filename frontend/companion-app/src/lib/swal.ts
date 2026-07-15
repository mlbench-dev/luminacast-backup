import Swal from "sweetalert2";

type ConfirmOptions = {
  title: string;
  text?: string;
  confirmButtonText?: string;
  cancelButtonText?: string;
  icon?: "warning" | "question" | "info" | "error" | "success";
};

export async function confirmAction({
  title,
  text,
  confirmButtonText = "Confirm",
  cancelButtonText = "Cancel",
  icon = "warning",
}: ConfirmOptions): Promise<boolean> {
  const result = await Swal.fire({
    title,
    text,
    icon,
    showCancelButton: true,
    confirmButtonText,
    cancelButtonText,
    reverseButtons: true,
    confirmButtonColor: "#7c3aed",
    cancelButtonColor: "#374151",
    background: "#111827",
    color: "#f9fafb",
  });

  return result.isConfirmed;
}
