import { useState } from "react";
import { Key } from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { toast } from "@/hooks/useToast";

export function StreamKeysPage() {
  const [rtmpKey, setRtmpKey] = useState("");

  return (
    <div className="space-y-6" data-testid="stream-keys-page">
      <div>
        <h1 className="text-2xl font-bold text-text">Stream Keys</h1>
        <p className="text-sm text-text-dim">Configure your TikTok RTMP stream key</p>
      </div>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">TikTok Stream Key</CardTitle>
          <CardDescription>Paste your RTMP URL from livecenter.tiktok.com (required per session)</CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <Input
            placeholder="rtmp://push-rtmp-f5.tiktok.com/live/..."
            value={rtmpKey}
            onChange={(e) => setRtmpKey(e.target.value)}
            data-testid="rtmp-key-input"
          />
          <Button
            variant="outline"
            data-testid="save-rtmp-button"
            onClick={() => toast({ title: "Stream key saved for this session", variant: "success" })}
          >
            <Key className="mr-2 h-4 w-4" />
            Save Key
          </Button>
        </CardContent>
      </Card>
    </div>
  );
}
