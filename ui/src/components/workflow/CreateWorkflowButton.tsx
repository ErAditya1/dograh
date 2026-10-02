'use client';

import { PlusIcon } from 'lucide-react';
import Link from 'next/link';

import { Button } from "@/components/ui/button";

export function CreateWorkflowButton() {
    return (
        <Button asChild className="gap-1.5 shadow-sm">
            <Link href="/workflow/create">
                <PlusIcon className="w-4 h-4" />
                <span>Create Agent</span>
            </Link>
        </Button>
    );
}
