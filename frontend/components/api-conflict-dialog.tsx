"use client";

import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import type { ConflictDescription } from "@/lib/api/conflicts";

export function ApiConflictDialog({
  conflict,
  onClose,
  onReload,
}: {
  conflict: ConflictDescription | null;
  onClose: () => void;
  onReload: () => void | Promise<void>;
}) {
  return (
    <AlertDialog open={Boolean(conflict)} onOpenChange={(open) => !open && onClose()}>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>서버에 더 최근 변경이 있습니다</AlertDialogTitle>
          <AlertDialogDescription>
            {conflict?.message} 입력 내용을 덮어쓰지 않고 최신 내용을 다시 불러올 수 있습니다.
          </AlertDialogDescription>
        </AlertDialogHeader>
        <AlertDialogFooter>
          <AlertDialogCancel onClick={onClose}>닫기</AlertDialogCancel>
          <AlertDialogAction onClick={() => void onReload()}>
            서버 내용 다시 불러오기
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}
