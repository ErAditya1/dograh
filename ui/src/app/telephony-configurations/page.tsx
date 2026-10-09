"use client";

import { HugeiconsIcon } from "@hugeicons/react";
import {
  CheckmarkCircle02Icon,
  ChevronRightIcon,
  Copy01Icon,
  Delete02Icon,
  ExternalLinkIcon,
  GlobeIcon,
  Loading02Icon,
  LockIcon,
  PencilIcon,
  PhoneIcon,
  PlusIcon,
  RotateCcwIcon,
  ShieldCheckIcon,
  ShoppingCart01Icon,
  SparklesIcon,
  StarIcon,
  TriangleAlertIcon,
  UserGroupIcon,
  ZapIcon,
} from "@hugeicons/core-free-icons";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import { toast } from "sonner";

import {
  deleteTelephonyConfigurationApiV1OrganizationsTelephonyConfigsConfigIdDelete,
  getTelephonyConfigurationByIdApiV1OrganizationsTelephonyConfigsConfigIdGet,
  getWorkflowsSummaryApiV1WorkflowSummaryGet,
  listTelephonyConfigurationsApiV1OrganizationsTelephonyConfigsGet,
  reactivateTelephonyConfigurationApiV1OrganizationsTelephonyConfigsConfigIdReactivatePost,
  setDefaultOutboundApiV1OrganizationsTelephonyConfigsConfigIdSetDefaultOutboundPost,
} from "@/client/sdk.gen";
import type {
  TelephonyConfigurationDetail,
  TelephonyConfigurationListItem,
} from "@/client/types.gen";
import { ConfigFormDialog } from "@/components/telephony/ConfigFormDialog";
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
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { useTelephonyConfigWarnings } from "@/context/TelephonyConfigWarningsContext";
import { detailFromError } from "@/lib/apiError";
import { useAuth } from "@/lib/auth";
import { copyTextToClipboard } from "@/lib/clipboard";

interface ClaimedNumberItem {
  id: number;
  phone_number: string;
  address_normalized?: string;
  country_code?: string;
  label?: string;
  carrier?: string;
  telephony_configuration_id: number;
  telephony_configuration_name?: string;
  pool_type: "shared_trial" | "shared_multi_org" | "dedicated" | string;
  monthly_price_cents: number;
  is_default_caller_id: boolean;
  is_default_outbound?: boolean;
  inbound_workflow_id?: number | null;
  inbound_workflow_name?: string | null;
  claimed_at?: string | null;
  rental_status?: string;
  next_rental_billing_at?: string | null;
  is_platform_inventory?: boolean;
  is_claimed?: boolean;
}

export default function TelephonyConfigurationsPage() {
  const { user, getAccessToken, loading: authLoading } = useAuth();
  const searchParams = useSearchParams();
  const {
    telnyxMissingWebhookPublicKeyCount,
    vonageMissingSignatureSecretCount,
    refresh: refreshWarnings,
  } = useTelephonyConfigWarnings();

  const [activeTab, setActiveTab] = useState<string>("claimed");
  const [items, setItems] = useState<TelephonyConfigurationListItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [createOpen, setCreateOpen] = useState(false);
  const [editTarget, setEditTarget] = useState<TelephonyConfigurationDetail | null>(null);
  const [editOpen, setEditOpen] = useState(false);
  const [deleteTarget, setDeleteTarget] = useState<TelephonyConfigurationListItem | null>(null);

  // Claimed Platform Numbers
  const [claimedNumbers, setClaimedNumbers] = useState<ClaimedNumberItem[]>([]);
  const [loadingClaimed, setLoadingClaimed] = useState(false);

  // Platform Numbers Marketplace
  const [platformNumbers, setPlatformNumbers] = useState<any[]>([]);
  const [loadingPlatformNumbers, setLoadingPlatformNumbers] = useState(false);
  const [categoryFilter, setCategoryFilter] = useState<string>("all");
  const [claimingNumberId, setClaimingNumberId] = useState<number | null>(null);
  const [purchasingNumber, setPurchasingNumber] = useState<any | null>(null);

  // Workflows for inbound routing
  const [workflows, setWorkflows] = useState<Array<{ id: number; name: string }>>([]);
  const [assignAgentTarget, setAssignAgentTarget] = useState<ClaimedNumberItem | null>(null);
  const [selectedWorkflowId, setSelectedWorkflowId] = useState<string>("__none__");
  const [savingAgent, setSavingAgent] = useState(false);

  // Releasing a claimed number
  const [releasingNumber, setReleasingNumber] = useState<ClaimedNumberItem | null>(null);
  const [releasing, setReleasing] = useState(false);

  // Setting default caller
  const [settingDefaultId, setSettingDefaultId] = useState<number | null>(null);

  // Load Workflows
  const loadWorkflows = useCallback(async () => {
    if (!user) return;
    try {
      const token = await getAccessToken();
      const res = await getWorkflowsSummaryApiV1WorkflowSummaryGet({
        headers: { Authorization: `Bearer ${token}` },
        query: { status: "active" },
      });
      const data = res.data ?? [];
      setWorkflows(data.map((w: any) => ({ id: w.id, name: w.name })));
    } catch (e) {
      console.error("Failed to load workflows", e);
    }
  }, [user, getAccessToken]);

  // Load Claimed Numbers
  const loadClaimedNumbers = useCallback(async () => {
    if (authLoading || !user) return;
    setLoadingClaimed(true);
    try {
      const token = await getAccessToken();
      const res = await fetch("/api/v1/platform/numbers/claimed", {
        headers: token ? { Authorization: `Bearer ${token}` } : undefined,
      });
      if (res.ok) {
        const data = await res.json();
        setClaimedNumbers(Array.isArray(data) ? data : []);
      }
    } catch (err) {
      console.error("Failed to load claimed numbers", err);
    } finally {
      setLoadingClaimed(false);
    }
  }, [authLoading, user, getAccessToken]);

  // Load Marketplace Platform Numbers
  const loadPlatformNumbers = useCallback(async () => {
    if (authLoading || !user) return;
    setLoadingPlatformNumbers(true);
    try {
      const token = await getAccessToken();
      const res = await fetch("/api/v1/platform/numbers", {
        headers: token ? { Authorization: `Bearer ${token}` } : undefined,
      });
      if (res.ok) {
        const data = await res.json();
        setPlatformNumbers(Array.isArray(data) ? data : []);
      }
    } catch (err) {
      console.error("Failed to load platform numbers", err);
    } finally {
      setLoadingPlatformNumbers(false);
    }
  }, [authLoading, user, getAccessToken]);

  // Load Configurations
  const fetchItems = useCallback(async () => {
    if (authLoading || !user) return;
    setLoading(true);
    try {
      const token = await getAccessToken();
      const res = await listTelephonyConfigurationsApiV1OrganizationsTelephonyConfigsGet({
        headers: { Authorization: `Bearer ${token}` },
      });
      if (res.error) throw new Error(detailFromError(res.error));
      setItems(res.data?.configurations ?? []);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Failed to load configurations");
    } finally {
      setLoading(false);
    }
  }, [authLoading, user, getAccessToken]);

  const onSaved = useCallback(async () => {
    await fetchItems();
    await loadPlatformNumbers();
    await loadClaimedNumbers();
    await refreshWarnings();
  }, [fetchItems, loadPlatformNumbers, loadClaimedNumbers, refreshWarnings]);

  useEffect(() => {
    fetchItems();
    loadPlatformNumbers();
    loadClaimedNumbers();
    loadWorkflows();
  }, [fetchItems, loadPlatformNumbers, loadClaimedNumbers, loadWorkflows]);

  // Tab query param support
  useEffect(() => {
    const tab = searchParams.get("tab");
    if (tab === "byo" || tab === "configurations") {
      setActiveTab("byo");
    } else if (tab === "claimed") {
      setActiveTab("claimed");
    }
    if (searchParams.get("add") === "1") {
      setActiveTab("byo");
      setCreateOpen(true);
    }
  }, [searchParams]);

  // Claim number
  const handleClaimNumber = async (num: any) => {
    try {
      setClaimingNumberId(num.id);
      const token = await getAccessToken();
      const res = await fetch(`/api/v1/platform/numbers/${num.id}/claim`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          ...(token ? { Authorization: `Bearer ${token}` } : {}),
        },
        body: JSON.stringify({
          set_as_default: true,
        }),
      });
      const data = await res.json();
      if (!res.ok) {
        throw new Error(data.detail || "Failed to provision number");
      }
      toast.success(data.message || `Successfully provisioned ${num.phone_number}!`);
      setPurchasingNumber(null);
      await loadClaimedNumbers();
      await loadPlatformNumbers();
      await fetchItems();
    } catch (err: any) {
      toast.error(err.message || "Failed to claim number");
    } finally {
      setClaimingNumberId(null);
    }
  };

  // Release claimed number
  const handleConfirmRelease = async () => {
    if (!releasingNumber) return;
    try {
      setReleasing(true);
      const token = await getAccessToken();
      const res = await fetch(`/api/v1/platform/numbers/${releasingNumber.id}/release`, {
        method: "POST",
        headers: token ? { Authorization: `Bearer ${token}` } : undefined,
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || "Failed to release number");
      toast.success(`Phone number ${releasingNumber.phone_number} released back to inventory`);
      setReleasingNumber(null);
      await loadClaimedNumbers();
      await loadPlatformNumbers();
      await fetchItems();
    } catch (e: any) {
      toast.error(e.message || "Failed to release number");
    } finally {
      setReleasing(false);
    }
  };

  // Set default caller
  const handleSetDefaultCaller = async (item: ClaimedNumberItem) => {
    try {
      setSettingDefaultId(item.id);
      const token = await getAccessToken();
      const res = await fetch(
        `/api/v1/organizations/telephony-configs/${item.telephony_configuration_id}/phone-numbers/${item.id}/set-default-caller`,
        {
          method: "POST",
          headers: token ? { Authorization: `Bearer ${token}` } : undefined,
        }
      );
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.detail || "Failed to set default caller ID");
      }
      toast.success(`${item.phone_number} is now your default caller ID`);
      await loadClaimedNumbers();
      await fetchItems();
    } catch (e: any) {
      toast.error(e.message || "Failed to set default caller ID");
    } finally {
      setSettingDefaultId(null);
    }
  };

  // Save inbound agent assignment
  const handleSaveInboundAgent = async () => {
    if (!assignAgentTarget) return;
    try {
      setSavingAgent(true);
      const token = await getAccessToken();
      const payload: any = {
        inbound_workflow_id:
          selectedWorkflowId === "__none__" ? null : Number(selectedWorkflowId),
        clear_inbound_workflow: selectedWorkflowId === "__none__",
      };
      const res = await fetch(
        `/api/v1/organizations/telephony-configs/${assignAgentTarget.telephony_configuration_id}/phone-numbers/${assignAgentTarget.id}`,
        {
          method: "PUT",
          headers: {
            "Content-Type": "application/json",
            ...(token ? { Authorization: `Bearer ${token}` } : {}),
          },
          body: JSON.stringify(payload),
        }
      );
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.detail || "Failed to update inbound routing");
      }
      toast.success(`Inbound routing updated for ${assignAgentTarget.phone_number}`);
      setAssignAgentTarget(null);
      await loadClaimedNumbers();
    } catch (e: any) {
      toast.error(e.message || "Failed to update inbound routing");
    } finally {
      setSavingAgent(false);
    }
  };

  // BYO handlers
  const onEdit = async (item: TelephonyConfigurationListItem) => {
    try {
      const token = await getAccessToken();
      const res = await getTelephonyConfigurationByIdApiV1OrganizationsTelephonyConfigsConfigIdGet(
        {
          headers: { Authorization: `Bearer ${token}` },
          path: { config_id: item.id },
        }
      );
      if (res.error) throw new Error(detailFromError(res.error));
      setEditTarget(res.data ?? null);
      setEditOpen(true);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Failed to load configuration");
    }
  };

  const onSetDefault = async (item: TelephonyConfigurationListItem) => {
    try {
      const token = await getAccessToken();
      const res = await setDefaultOutboundApiV1OrganizationsTelephonyConfigsConfigIdSetDefaultOutboundPost(
        {
          headers: { Authorization: `Bearer ${token}` },
          path: { config_id: item.id },
        }
      );
      if (res.error) throw new Error(detailFromError(res.error));
      toast.success(`${item.name} is now the default outbound configuration`);
      fetchItems();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Failed to set default");
    }
  };

  const onReactivate = async (item: TelephonyConfigurationListItem) => {
    try {
      const token = await getAccessToken();
      const res = await reactivateTelephonyConfigurationApiV1OrganizationsTelephonyConfigsConfigIdReactivatePost(
        {
          headers: { Authorization: `Bearer ${token}` },
          path: { config_id: item.id },
        }
      );
      if (res.error) throw new Error(detailFromError(res.error));
      toast.success(`${item.name} reactivated — reconnecting within a minute`);
      fetchItems();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Failed to reactivate configuration");
    }
  };

  const onConfirmDelete = async () => {
    if (!deleteTarget) return;
    try {
      const token = await getAccessToken();
      const res = await deleteTelephonyConfigurationApiV1OrganizationsTelephonyConfigsConfigIdDelete(
        {
          headers: { Authorization: `Bearer ${token}` },
          path: { config_id: deleteTarget.id },
        }
      );
      if (res.error) throw new Error(detailFromError(res.error));
      toast.success("Configuration deleted");
      setDeleteTarget(null);
      fetchItems();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Failed to delete configuration");
    }
  };

  // Strictly filter BYO configurations: Platform-claimed configurations are completely removed
  const byoConfigs = items.filter(
    (item) =>
      !item.is_claimed &&
      !item.is_platform_inventory &&
      !item.name.startsWith("Platform - ")
  );

  // Filter marketplace numbers by category
  const filteredPlatformNumbers = platformNumbers.filter((n) => {
    if (categoryFilter === "all") return true;
    if (categoryFilter === "shared_trial") return n.pool_type === "shared_trial";
    if (categoryFilter === "shared_multi_org") return n.pool_type === "shared_multi_org";
    if (categoryFilter === "dedicated") return n.pool_type === "dedicated" || (!n.pool_type?.includes("shared"));
    return true;
  });

  return (
    <div className="min-h-screen">
      <div className="app-page">
        {/* Top Header */}
        <div className="flex flex-col md:flex-row md:items-center justify-between gap-4 mb-6">
          <div>
            <h1 className="text-3xl font-bold tracking-tight">Telephony &amp; Phone Numbers</h1>
            <p className="text-sm text-muted-foreground mt-1">
              Manage platform-allocated phone numbers and connected Bring-Your-Own (BYO) carrier accounts.
            </p>
          </div>
          {activeTab === "byo" && (
            <Button onClick={() => setCreateOpen(true)} className="gap-2">
              <HugeiconsIcon icon={PlusIcon} className="h-4 w-4" /> Connect Carrier
            </Button>
          )}
        </div>

        {/* Top Level Tabs */}
        <Tabs value={activeTab} onValueChange={setActiveTab} className="w-full space-y-6">
          <TabsList className="bg-muted/70 p-1 rounded-xl border">
            <TabsTrigger value="claimed" className="gap-2 px-4 py-2 font-medium">
              <HugeiconsIcon icon={PhoneIcon} className="h-4 w-4" />
              Claimed Platform Numbers
              <Badge variant="secondary" className="ml-1 px-1.5 py-0.2 text-[11px] font-semibold">
                {claimedNumbers.length}
              </Badge>
            </TabsTrigger>
            <TabsTrigger value="byo" className="gap-2 px-4 py-2 font-medium">
              <HugeiconsIcon icon={ZapIcon} className="h-4 w-4" />
              Your Connected Configurations (BYO)
              <Badge variant="outline" className="ml-1 px-1.5 py-0.2 text-[11px]">
                {byoConfigs.length}
              </Badge>
            </TabsTrigger>
          </TabsList>

          {/* TAB 1: CLAIMED PLATFORM NUMBERS */}
          <TabsContent value="claimed" className="space-y-8 focus-visible:outline-none">
            {/* Section 1: Active Claimed Numbers in this workspace */}
            <div className="space-y-4">
              <div className="flex items-center justify-between">
                <div>
                  <h2 className="text-lg font-semibold tracking-tight">Active Claimed Numbers</h2>
                  <p className="text-xs text-muted-foreground">
                    Numbers provisioned into your workspace. Inbound routing and default caller IDs are configurable below.
                  </p>
                </div>
                <Badge variant="outline" className="font-mono text-xs">
                  {claimedNumbers.length} Allocated
                </Badge>
              </div>

              {loadingClaimed ? (
                <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
                  <Skeleton className="h-40 rounded-xl" />
                  <Skeleton className="h-40 rounded-xl" />
                </div>
              ) : claimedNumbers.length === 0 ? (
                <div className="rounded-xl border border-dashed p-8 text-center bg-muted/20 space-y-3">
                  <HugeiconsIcon icon={PhoneIcon} className="h-8 w-8 mx-auto text-muted-foreground/60" />
                  <div>
                    <h3 className="text-sm font-semibold">No Platform Numbers Claimed Yet</h3>
                    <p className="text-xs text-muted-foreground max-w-md mx-auto mt-1">
                      Your workspace has not claimed any platform phone numbers yet. Choose a pre-verified number from the inventory below to start calling instantly!
                    </p>
                  </div>
                </div>
              ) : (
                <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
                  {claimedNumbers.map((num) => {
                    const isShared = num.pool_type === "shared_trial";
                    const isMultiOrg = num.pool_type === "shared_multi_org";
                    const isDefault = Boolean(num.is_default_caller_id);

                    return (
                      <Card
                        key={num.id}
                        className={`flex flex-col justify-between overflow-hidden transition-all shadow-sm ${
                          isDefault ? "ring-2 ring-emerald-500/30 border-emerald-500/40" : "hover:border-primary/40"
                        }`}
                      >
                        <CardHeader className="pb-3 pt-4 px-5">
                          <div className="flex items-start justify-between gap-2">
                            <div>
                              <div className="flex items-center gap-2">
                                <span className="font-mono text-lg font-bold tracking-tight text-foreground">
                                  {num.phone_number}
                                </span>
                                {isDefault && (
                                  <Badge className="bg-emerald-600 hover:bg-emerald-600 text-white text-[10px] px-1.5 py-0 h-4">
                                    Default
                                  </Badge>
                                )}
                              </div>
                              <div className="flex items-center gap-2 text-xs text-muted-foreground mt-0.5">
                                <span className="capitalize font-medium text-foreground">
                                  {num.carrier || "Platform"}
                                </span>
                                <span>•</span>
                                <span>
                                  {isShared
                                    ? "Free Sandbox"
                                    : num.monthly_price_cents > 0
                                    ? `$${(num.monthly_price_cents / 100).toFixed(2)}/mo`
                                    : "Included in Plan"}
                                </span>
                              </div>
                            </div>

                            <Badge
                              variant={isShared ? "secondary" : "outline"}
                              className={`text-xs capitalize ${
                                isShared
                                  ? "bg-muted text-muted-foreground font-semibold"
                                  : isMultiOrg
                                  ? "border-teal-500/40 bg-teal-500/10 text-teal-600 font-semibold"
                                  : "border-purple-500/40 bg-purple-500/10 text-purple-600 font-semibold"
                              }`}
                            >
                              {isShared ? "Shared Trial" : isMultiOrg ? "Multi-Org Shared" : "Dedicated"}
                            </Badge>
                          </div>
                        </CardHeader>

                        <CardContent className="space-y-3 pb-4 px-5 text-xs">
                          {/* Inbound Agent Routing */}
                          <div className="rounded-lg bg-muted/40 p-2.5 border space-y-1.5">
                            <div className="flex items-center justify-between text-muted-foreground">
                              <span className="font-medium">Inbound Agent:</span>
                              <Button
                                variant="ghost"
                                size="sm"
                                onClick={() => {
                                  setAssignAgentTarget(num);
                                  setSelectedWorkflowId(
                                    num.inbound_workflow_id ? String(num.inbound_workflow_id) : "__none__"
                                  );
                                }}
                                className="h-5 px-1.5 text-[11px] text-primary hover:text-primary/80"
                              >
                                {num.inbound_workflow_id ? "Change Agent" : "Assign Agent"}
                              </Button>
                            </div>
                            <div className="font-semibold text-foreground truncate">
                              {num.inbound_workflow_name ? (
                                <span className="text-emerald-700 dark:text-emerald-300">
                                  ✓ {num.inbound_workflow_name}
                                </span>
                              ) : (
                                <span className="text-muted-foreground italic">
                                  No inbound agent assigned
                                </span>
                              )}
                            </div>
                          </div>

                          {/* Footer Actions */}
                          <div className="pt-2 border-t flex items-center justify-between gap-2">
                            {!isDefault ? (
                              <Button
                                variant="outline"
                                size="sm"
                                disabled={settingDefaultId === num.id}
                                onClick={() => handleSetDefaultCaller(num)}
                                className="h-7 text-xs px-2.5 gap-1"
                              >
                                {settingDefaultId === num.id ? (
                                  <HugeiconsIcon icon={Loading02Icon} className="h-3 w-3 animate-spin" />
                                ) : (
                                  <HugeiconsIcon icon={CheckmarkCircle02Icon} className="h-3 w-3 text-emerald-600" />
                                )}
                                Set as Default
                              </Button>
                            ) : (
                              <span className="text-[11px] text-emerald-600 font-medium flex items-center gap-1">
                                <HugeiconsIcon icon={CheckmarkCircle02Icon} className="h-3.5 w-3.5" /> Default Outbound
                              </span>
                            )}

                            <Button
                              variant="ghost"
                              size="sm"
                              onClick={() => setReleasingNumber(num)}
                              className="h-7 text-xs text-red-600 hover:text-red-700 hover:bg-red-50 dark:hover:bg-red-950/30 px-2"
                            >
                              Release
                            </Button>
                          </div>
                        </CardContent>
                      </Card>
                    );
                  })}
                </div>
              )}
            </div>

            {/* Section 2: Marketplace / Platform Numbers Showcase */}
            <div className="rounded-xl border border-border bg-card p-6 shadow-sm space-y-5">
              <div className="flex flex-col md:flex-row md:items-center justify-between gap-4">
                <div>
                  <div className="flex items-center gap-2 mb-1">
                    <span className="flex h-7 w-7 items-center justify-center rounded-lg bg-primary/10 text-primary">
                      <HugeiconsIcon icon={SparklesIcon} className="h-4 w-4" />
                    </span>
                    <h2 className="text-xl font-bold tracking-tight">Browse Platform Inventory</h2>
                    <Badge variant="outline" className="text-xs">
                      Instant Setup
                    </Badge>
                  </div>
                  <p className="text-sm text-muted-foreground max-w-2xl">
                    Claim pre-provisioned numbers instantly. Choose free Shared Trial for sandbox testing, Multi-Org Shared for high-scale campaigns, or Dedicated for exclusive brand caller ID.
                  </p>
                </div>

                {/* Category Filters */}
                <div className="flex items-center gap-1 bg-muted p-1 rounded-lg self-start md:self-auto text-xs">
                  <Button
                    type="button"
                    variant={categoryFilter === "all" ? "secondary" : "ghost"}
                    size="sm"
                    className="h-7 px-2.5 text-xs font-medium"
                    onClick={() => setCategoryFilter("all")}
                  >
                    All ({platformNumbers.length})
                  </Button>
                  <Button
                    type="button"
                    variant={categoryFilter === "shared_trial" ? "secondary" : "ghost"}
                    size="sm"
                    className="h-7 px-2.5 text-xs font-medium"
                    onClick={() => setCategoryFilter("shared_trial")}
                  >
                    Shared Trial
                  </Button>
                  <Button
                    type="button"
                    variant={categoryFilter === "shared_multi_org" ? "secondary" : "ghost"}
                    size="sm"
                    className="h-7 px-2.5 text-xs font-medium"
                    onClick={() => setCategoryFilter("shared_multi_org")}
                  >
                    Multi-Org
                  </Button>
                  <Button
                    type="button"
                    variant={categoryFilter === "dedicated" ? "secondary" : "ghost"}
                    size="sm"
                    className="h-7 px-2.5 text-xs font-medium"
                    onClick={() => setCategoryFilter("dedicated")}
                  >
                    Dedicated
                  </Button>
                </div>
              </div>

              {loadingPlatformNumbers ? (
                <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
                  <Skeleton className="h-32 w-full rounded-lg" />
                  <Skeleton className="h-32 w-full rounded-lg" />
                  <Skeleton className="h-32 w-full rounded-lg" />
                </div>
              ) : filteredPlatformNumbers.length === 0 ? (
                <div className="rounded-lg border border-dashed p-6 text-center">
                  <HugeiconsIcon icon={PhoneIcon} className="h-7 w-7 mx-auto text-muted-foreground/60 mb-2" />
                  <p className="text-sm font-medium">No Numbers in this Category</p>
                  <p className="text-xs text-muted-foreground mt-1">
                    Superadmins can stock more numbers in the Superadmin Inventory.
                  </p>
                </div>
              ) : (
                <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3.5">
                  {filteredPlatformNumbers.map((num) => {
                    const isShared = num.pool_type === "shared_trial";
                    const isMultiOrg = num.pool_type === "shared_multi_org";
                    const isClaiming = claimingNumberId === num.id;
                    const isClaimedByCurrent = Boolean(num.is_claimed_by_you);
                    const isDedicatedTakenByOther = !isShared && !isMultiOrg && num.in_use && !isClaimedByCurrent;

                    return (
                      <div
                        key={num.id}
                        className="flex flex-col justify-between p-4 rounded-xl border bg-card hover:border-primary/40 transition-all shadow-sm"
                      >
                        <div>
                          <div className="flex items-center justify-between gap-2 mb-2">
                            <span className="font-mono text-base font-bold tracking-tight">
                              {num.phone_number}
                            </span>
                            <Badge
                              variant={isShared ? "default" : "outline"}
                              className={`text-xs ${
                                isShared
                                  ? "bg-neutral-900 text-white font-semibold"
                                  : isMultiOrg
                                  ? "border-teal-500/40 bg-teal-500/10 text-teal-600 font-semibold"
                                  : "border-purple-500/40 bg-purple-500/10 text-purple-600 font-semibold"
                              }`}
                            >
                              {isShared ? "Shared Trial" : isMultiOrg ? "Multi-Org Shared" : "Dedicated"}
                            </Badge>
                          </div>

                          <div className="flex items-center gap-2 text-xs text-muted-foreground mb-2">
                            <span className="capitalize font-medium text-foreground">{num.carrier}</span>
                            <span>•</span>
                            <span>
                              {isShared ? "Free for testing" : `$${(num.monthly_price_cents / 100).toFixed(2)}/mo`}
                            </span>
                          </div>

                          {isShared && (
                            <div className="mb-3 text-[11px] leading-tight text-blue-700 dark:text-blue-300 bg-blue-50/60 dark:bg-blue-950/30 border border-blue-200/60 rounded p-1.5">
                              Free sandbox testing. Ideal for checking audio quality and agent latency.
                            </div>
                          )}

                          {isMultiOrg && (
                            <div className="mb-3 text-[11px] leading-tight text-teal-700 dark:text-teal-300 bg-teal-50/60 dark:bg-teal-950/30 border border-teal-200/60 rounded p-1.5">
                              Multi-Org Shared Pool. High concurrency capacity shared across verified organizations.
                            </div>
                          )}

                          {!isShared && !isMultiOrg && (
                            <div className="mb-3 text-[11px] leading-tight text-purple-700 dark:text-purple-300 bg-purple-50/60 dark:bg-purple-950/30 border border-purple-200/60 rounded p-1.5">
                              Dedicated Caller ID. Exclusively leased to your organization for live campaigns.
                            </div>
                          )}
                        </div>

                        <div className="pt-2 border-t flex items-center justify-between">
                          <span className="text-xs text-muted-foreground flex items-center gap-1">
                            <HugeiconsIcon icon={ShieldCheckIcon} className="h-3.5 w-3.5 text-primary" />
                            {isShared ? "Sandbox" : isMultiOrg ? "Shared Pool" : "Exclusive"}
                          </span>

                          {isShared ? (
                            <Button
                              size="sm"
                              variant="secondary"
                              onClick={() => {
                                copyTextToClipboard(num.phone_number);
                                toast.success(`Copied test number ${num.phone_number} to clipboard!`);
                              }}
                              className="h-8 gap-1.5 text-xs font-medium"
                            >
                              <HugeiconsIcon icon={Copy01Icon} className="h-3.5 w-3.5" /> Copy Number
                            </Button>
                          ) : isClaimedByCurrent ? (
                            <Badge
                              variant="secondary"
                              className="text-xs bg-emerald-500/15 text-emerald-700 dark:text-emerald-300 border border-emerald-500/30 font-medium"
                            >
                              Claimed by Workspace
                            </Badge>
                          ) : isDedicatedTakenByOther ? (
                            <Badge variant="outline" className="text-xs opacity-60">
                              Claimed by Another Org
                            </Badge>
                          ) : (
                            <Button
                              size="sm"
                              variant="outline"
                              disabled={isClaiming}
                              onClick={() => setPurchasingNumber(num)}
                              className="h-8 gap-1.5 text-xs font-medium border-primary/30 hover:bg-primary/10"
                            >
                              {isClaiming ? (
                                <>
                                  <HugeiconsIcon icon={Loading02Icon} className="h-3.5 w-3.5 animate-spin" /> Claiming...
                                </>
                              ) : (
                                <>
                                  <HugeiconsIcon icon={ShoppingCart01Icon} className="h-3.5 w-3.5" /> Claim Number
                                </>
                              )}
                            </Button>
                          )}
                        </div>
                      </div>
                    );
                  })}
                </div>
              )}
            </div>
          </TabsContent>

          {/* TAB 2: CONNECTED BYO CONFIGURATIONS */}
          <TabsContent value="byo" className="space-y-6 focus-visible:outline-none">
            {telnyxMissingWebhookPublicKeyCount > 0 && (
              <div className="rounded-md border border-amber-300 bg-amber-50 p-4 text-amber-900 dark:border-amber-800 dark:bg-amber-950 dark:text-amber-200">
                <div className="flex items-start gap-3">
                  <HugeiconsIcon icon={TriangleAlertIcon} className="h-5 w-5 shrink-0 mt-0.5" />
                  <div className="space-y-1 text-sm">
                    <p className="font-medium">Webhook public key not configured</p>
                    <p>
                      {telnyxMissingWebhookPublicKeyCount === 1
                        ? "1 Telnyx configuration is"
                        : `${telnyxMissingWebhookPublicKeyCount} Telnyx configurations are`}{" "}
                      missing a webhook public key. Without it, Telnyx call status updates and inbound calls are being rejected.
                    </p>
                  </div>
                </div>
              </div>
            )}

            {vonageMissingSignatureSecretCount > 0 && (
              <div className="rounded-md border border-amber-300 bg-amber-50 p-4 text-amber-900 dark:border-amber-800 dark:bg-amber-950 dark:text-amber-200">
                <div className="flex items-start gap-3">
                  <HugeiconsIcon icon={TriangleAlertIcon} className="h-5 w-5 shrink-0 mt-0.5" />
                  <div className="space-y-1 text-sm">
                    <p className="font-medium">Signature secret not configured</p>
                    <p>
                      {vonageMissingSignatureSecretCount === 1
                        ? "1 Vonage configuration is"
                        : `${vonageMissingSignatureSecretCount} Vonage configurations are`}{" "}
                      missing a signature secret. Without it, Vonage signed webhooks will fail authentication.
                    </p>
                  </div>
                </div>
              </div>
            )}

            {loading ? (
              <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
                <Skeleton className="h-48 rounded-xl" />
                <Skeleton className="h-48 rounded-xl" />
              </div>
            ) : byoConfigs.length === 0 ? (
              <div className="rounded-xl border border-dashed p-10 text-center bg-muted/20 space-y-4">
                <HugeiconsIcon icon={ZapIcon} className="h-10 w-10 mx-auto text-muted-foreground/60" />
                <div>
                  <h3 className="text-base font-semibold">No Connected BYO Carriers</h3>
                  <p className="text-xs text-muted-foreground max-w-md mx-auto mt-1">
                    You haven&apos;t connected your own provider accounts (Twilio, Smartflo, Plivo, Asterisk) yet. Use the button below to connect your carrier.
                  </p>
                </div>
                <Button onClick={() => setCreateOpen(true)} className="gap-2">
                  <HugeiconsIcon icon={PlusIcon} className="h-4 w-4" /> Connect Carrier Account
                </Button>
              </div>
            ) : (
              <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
                {byoConfigs.map((item) => (
                  <Card
                    key={item.id}
                    className={`flex flex-col justify-between overflow-hidden shadow-sm hover:border-primary/40 transition-all ${
                      item.is_default_outbound ? "ring-2 ring-primary/20 border-primary" : ""
                    }`}
                  >
                    <CardHeader className="pb-3">
                      <div className="flex items-start justify-between gap-2">
                        <div className="space-y-1">
                          <div className="flex items-center gap-2">
                            <CardTitle className="text-lg font-bold truncate">
                              {item.name}
                            </CardTitle>
                            {item.is_default_outbound && (
                              <Badge className="bg-primary text-primary-foreground text-[10px] px-1.5 py-0 h-4">
                                Default
                              </Badge>
                            )}
                          </div>
                          <CardDescription className="capitalize flex items-center gap-1.5 text-xs">
                            <span>{item.provider}</span>
                            <span>•</span>
                            <span className="uppercase text-[10px] font-mono">{item.connectivity}</span>
                          </CardDescription>
                        </div>
                      </div>
                    </CardHeader>

                    <CardContent className="space-y-4 text-xs">
                      <div className="grid grid-cols-2 gap-2 rounded-lg bg-muted/50 p-2.5">
                        <div>
                          <p className="text-muted-foreground text-[11px]">Phone Numbers</p>
                          <p className="font-semibold text-foreground text-sm">
                            {item.active_phone_number_count}
                          </p>
                        </div>
                        {item.supports_trunks && (
                          <div>
                            <p className="text-muted-foreground text-[11px]">SIP Trunks</p>
                            <p className="font-semibold text-foreground text-sm">
                              {item.enabled_trunk_count}
                            </p>
                          </div>
                        )}
                      </div>

                      <div className="pt-2 border-t flex items-center justify-between gap-2">
                        <div className="flex items-center gap-1">
                          <Button
                            variant="ghost"
                            size="icon"
                            className="h-8 w-8 text-muted-foreground hover:text-foreground"
                            onClick={() => onEdit(item)}
                            title="Edit Credentials"
                          >
                            <HugeiconsIcon icon={PencilIcon} className="h-4 w-4" />
                          </Button>

                          {!item.is_default_outbound && (
                            <Button
                              variant="ghost"
                              size="icon"
                              className="h-8 w-8 text-muted-foreground hover:text-foreground"
                              onClick={() => onSetDefault(item)}
                              title="Set as Default Outbound"
                            >
                              <HugeiconsIcon icon={StarIcon} className="h-4 w-4" />
                            </Button>
                          )}

                          <Button
                            variant="ghost"
                            size="icon"
                            className="h-8 w-8 text-red-600 hover:text-red-700 hover:bg-red-50"
                            onClick={() => setDeleteTarget(item)}
                            title="Delete"
                          >
                            <HugeiconsIcon icon={Delete02Icon} className="h-4 w-4" />
                          </Button>
                        </div>

                        <Button variant="outline" size="sm" asChild className="h-8 text-xs">
                          <Link href={`/telephony-configurations/${item.id}`}>
                            Manage Carrier
                            <HugeiconsIcon icon={ChevronRightIcon} className="h-3.5 w-3.5 ml-1" />
                          </Link>
                        </Button>
                      </div>
                    </CardContent>
                  </Card>
                ))}
              </div>
            )}
          </TabsContent>
        </Tabs>
      </div>

      {/* DIALOGS */}

      {/* BYO Carrier Form Dialog */}
      <ConfigFormDialog
        open={createOpen}
        onOpenChange={setCreateOpen}
        existing={null}
        suggestDefaultOutbound={!items.some((item) => item.is_default_outbound)}
        onSaved={onSaved}
      />
      <ConfigFormDialog
        open={editOpen}
        onOpenChange={setEditOpen}
        existing={editTarget}
        onSaved={onSaved}
      />

      {/* Assign Inbound Agent Dialog */}
      <Dialog
        open={!!assignAgentTarget}
        onOpenChange={(o) => !o && setAssignAgentTarget(null)}
      >
        <DialogContent className="sm:max-w-[420px]">
          <DialogHeader>
            <DialogTitle>Assign Inbound Voice Agent</DialogTitle>
            <DialogDescription>
              Select which AI agent workflow will answer incoming calls to{" "}
              <span className="font-mono font-semibold text-foreground">
                {assignAgentTarget?.phone_number}
              </span>.
            </DialogDescription>
          </DialogHeader>

          <div className="py-3 space-y-3">
            <Select
              value={selectedWorkflowId}
              onValueChange={setSelectedWorkflowId}
            >
              <SelectTrigger className="w-full">
                <SelectValue placeholder="Select an AI Agent workflow" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="__none__">None (Do not answer inbound)</SelectItem>
                {workflows.map((w) => (
                  <SelectItem key={w.id} value={String(w.id)}>
                    {w.name} (#{w.id})
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>

          <DialogFooter>
            <Button
              variant="outline"
              onClick={() => setAssignAgentTarget(null)}
              disabled={savingAgent}
            >
              Cancel
            </Button>
            <Button
              onClick={handleSaveInboundAgent}
              disabled={savingAgent}
            >
              {savingAgent ? (
                <>
                  <HugeiconsIcon icon={Loading02Icon} className="h-4 w-4 animate-spin mr-1.5" />
                  Saving...
                </>
              ) : (
                "Save Inbound Routing"
              )}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Release Confirmation Dialog */}
      <AlertDialog
        open={!!releasingNumber}
        onOpenChange={(o) => !o && setReleasingNumber(null)}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Release Phone Number?</AlertDialogTitle>
            <AlertDialogDescription className="space-y-2">
              <p>
                Are you sure you want to release{" "}
                <span className="font-mono font-bold text-foreground">
                  {releasingNumber?.phone_number}
                </span>{" "}
                back to the platform inventory?
              </p>
              <p className="text-xs text-muted-foreground">
                Inbound routing will stop, and other organizations will be able to claim this number.
              </p>
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={releasing}>Cancel</AlertDialogCancel>
            <AlertDialogAction
              onClick={handleConfirmRelease}
              disabled={releasing}
              className="bg-red-600 hover:bg-red-700 text-white"
            >
              {releasing ? "Releasing..." : "Yes, Release Number"}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      {/* Delete BYO Config Dialog */}
      <AlertDialog
        open={!!deleteTarget}
        onOpenChange={(o) => !o && setDeleteTarget(null)}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Delete configuration?</AlertDialogTitle>
            <AlertDialogDescription>
              {deleteTarget?.name} and all of its custom phone numbers will be removed.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction onClick={onConfirmDelete}>Delete</AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      {/* Purchase / Claim Confirmation Dialog */}
      <AlertDialog
        open={!!purchasingNumber}
        onOpenChange={(o) => !o && setPurchasingNumber(null)}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle className="flex items-center gap-2">
              <HugeiconsIcon icon={ShoppingCart01Icon} className="h-5 w-5 text-primary" />
              Confirm Phone Number Claim
            </AlertDialogTitle>
            <AlertDialogDescription className="space-y-3 pt-2">
              <div className="rounded-lg bg-muted/60 p-3 text-sm space-y-1.5 border">
                <div className="flex justify-between">
                  <span className="text-muted-foreground">Phone Number:</span>
                  <span className="font-mono font-bold text-foreground">{purchasingNumber?.phone_number}</span>
                </div>
                <div className="flex justify-between">
                  <span className="text-muted-foreground">Category:</span>
                  <span className="font-semibold capitalize text-foreground">
                    {purchasingNumber?.pool_type === "shared_trial"
                      ? "Shared Trial (Sandbox)"
                      : purchasingNumber?.pool_type === "shared_multi_org"
                      ? "Multi-Org Shared"
                      : "Dedicated Caller ID"}
                  </span>
                </div>
                <div className="flex justify-between">
                  <span className="text-muted-foreground">Carrier:</span>
                  <span className="font-medium text-foreground capitalize">{purchasingNumber?.carrier}</span>
                </div>
                <div className="flex justify-between pt-1 border-t">
                  <span className="text-muted-foreground">Pricing:</span>
                  <span className="font-bold text-emerald-600 dark:text-emerald-400">
                    {purchasingNumber?.pool_type === "shared_trial"
                      ? "Free Sandbox"
                      : purchasingNumber?.monthly_price_cents > 0
                      ? `$${(purchasingNumber.monthly_price_cents / 100).toFixed(2)} / month`
                      : "Included in Plan"}
                  </span>
                </div>
              </div>
              <p className="text-xs text-muted-foreground">
                This number will be provisioned directly into your workspace. It will be pre-configured for instant outbound calls and ready for inbound agent assignment.
              </p>
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction
              onClick={() => {
                if (purchasingNumber) handleClaimNumber(purchasingNumber);
              }}
              className="bg-primary text-primary-foreground hover:bg-primary/90"
            >
              Confirm &amp; Provision
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}
