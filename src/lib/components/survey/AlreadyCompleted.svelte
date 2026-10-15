<script lang="ts">
  import { Button } from '@immich/ui';
  import FutoLogo from '$lib/components/layout/FutoLogo.svelte';

  interface Props {
    slug: string;
  }

  let { slug }: Props = $props();

  async function startOver() {
    await fetch(`/api/s/${slug}/reset`, { method: 'POST', credentials: 'same-origin' });
    window.location.reload();
  }
</script>

<div class="flex flex-1 flex-col items-center justify-center gap-6 p-6 text-center">
  <FutoLogo size="large" />

  <h1 class="text-3xl font-bold">You've already completed this survey</h1>
  <p class="text-gray-500">Your responses were recorded. Thank you for your feedback!</p>

  <Button variant="outline" onclick={startOver}>Start over</Button>
</div>
