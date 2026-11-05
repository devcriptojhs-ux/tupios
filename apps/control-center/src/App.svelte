<script lang="ts">
	import { onMount } from 'svelte';
	import { invoke } from '@tauri-apps/api/core';

	let systemStatus: any = null;
	let commits: any[] = [];
	let loading = true;
	let error = '';

	onMount(async () => {
		try {
			const [status, commitList] = await Promise.all([
				invoke('get_system_status'),
				invoke('list_commits')
			]);
			systemStatus = status;
			commits = commitList as any[];
		} catch (e) {
			error = String(e);
		} finally {
			loading = false;
		}
	});

	async function approveCommit(commitId: string) {
		try {
			const result = await invoke('approve_commit', { commitId });
			alert(result as string);
			// Refresh commits list
			commits = (await invoke('list_commits')) as any[];
		} catch (e) {
			alert(`Error: ${e}`);
		}
	}

	function getStatusColor(status: string): string {
		switch (status) {
			case 'running':
			case 'active':
				return '#52EA23';
			case 'dormant':
			case 'standby':
				return '#FFA500';
			case 'error':
				return '#FF4444';
			default:
				return '#888';
		}
	}
</script>

<div class="container">
	<header>
		<h1>TUPIOS Control Center</h1>
		<p class="subtitle">AI-Native Operating System — Management Interface</p>
	</header>

	{#if loading}
		<div class="loading">Loading system status...</div>
	{:else if error}
		<div class="error">Error: {error}</div>
	{:else}
		<section class="status-grid">
			<h2>System Status</h2>
			<div class="cards">
				{#each Object.entries(systemStatus) as [key, value]}
					<div class="card">
						<div class="card-header">
							<span class="label">{key.toUpperCase()}</span>
							<span class="status-dot" style="background: {getStatusColor(value.status)}"></span>
						</div>
						<div class="card-body">
							<p><strong>Status:</strong> {value.status}</p>
							{#if value.uptime}
								<p><strong>Uptime:</strong> {value.uptime}</p>
							{/if}
							{#if value.backend}
								<p><strong>Backend:</strong> {value.backend}</p>
							{/if}
							{#if value.model}
								<p><strong>Model:</strong> {value.model}</p>
							{/if}
							{#if value.pending_commits !== undefined}
								<p><strong>Pending:</strong> {value.pending_commits}</p>
							{/if}
						</div>
					</div>
				{/each}
			</div>
		</section>

		<section class="commits-section">
			<h2>Pending Commits</h2>
			{#if commits.length === 0}
				<p class="empty">No pending commits</p>
			{:else}
				<div class="commit-list">
					{#each commits as commit}
						<div class="commit-card">
							<div class="commit-header">
								<span class="commit-id">{commit.id}</span>
								<span class="commit-status">{commit.status}</span>
							</div>
							<p class="commit-message">{commit.message}</p>
							<div class="commit-meta">
								<span>Author: {commit.author}</span>
								<span>{commit.timestamp}</span>
							</div>
							{#if commit.status === 'pending'}
								<button on:click={() => approveCommit(commit.id)}>Approve & Apply</button>
							{/if}
						</div>
					{/each}
				</div>
			{/if}
		</section>
	{/if}
</div>

<style>
	* {
		margin: 0;
		padding: 0;
		box-sizing: border-box;
	}

	body {
		font-family: 'JetBrains Mono', 'Fira Code', monospace;
		background: #000;
		color: #fff;
	}

	.container {
		max-width: 1200px;
		margin: 0 auto;
		padding: 2rem;
	}

	header {
		margin-bottom: 2rem;
		border-bottom: 1px solid #333;
		padding-bottom: 1rem;
	}

	h1 {
		color: #52EA23;
		font-size: 2rem;
		letter-spacing: 2px;
	}

	.subtitle {
		color: #888;
		font-size: 0.9rem;
		margin-top: 0.5rem;
	}

	h2 {
		color: #52EA23;
		font-size: 1.2rem;
		margin-bottom: 1rem;
	}

	.loading,
	.error {
		padding: 2rem;
		text-align: center;
		color: #aaa;
	}

	.error {
		color: #FF4444;
	}

	.status-grid {
		margin-bottom: 2rem;
	}

	.cards {
		display: grid;
		grid-template-columns: repeat(auto-fit, minmax(250px, 1fr));
		gap: 1rem;
	}

	.card {
		background: #111;
		border: 1px solid #333;
		border-radius: 4px;
		padding: 1rem;
	}

	.card-header {
		display: flex;
		justify-content: space-between;
		align-items: center;
		margin-bottom: 0.75rem;
	}

	.label {
		font-weight: bold;
		color: #fff;
		font-size: 0.9rem;
	}

	.status-dot {
		width: 12px;
		height: 12px;
		border-radius: 50%;
	}

	.card-body p {
		font-size: 0.8rem;
		color: #aaa;
		margin-bottom: 0.3rem;
	}

	.commits-section {
		margin-top: 2rem;
	}

	.empty {
		color: #666;
		font-style: italic;
	}

	.commit-list {
		display: flex;
		flex-direction: column;
		gap: 1rem;
	}

	.commit-card {
		background: #111;
		border: 1px solid #333;
		border-radius: 4px;
		padding: 1rem;
	}

	.commit-header {
		display: flex;
		justify-content: space-between;
		align-items: center;
		margin-bottom: 0.5rem;
	}

	.commit-id {
		font-weight: bold;
		color: #52EA23;
		font-size: 0.9rem;
	}

	.commit-status {
		font-size: 0.75rem;
		padding: 0.2rem 0.5rem;
		border-radius: 3px;
		background: #2d1f0a;
		color: #FFA500;
		border: 1px solid #FFA500;
	}

	.commit-message {
		color: #fff;
		font-size: 0.85rem;
		margin-bottom: 0.5rem;
	}

	.commit-meta {
		display: flex;
		justify-content: space-between;
		font-size: 0.75rem;
		color: #666;
		margin-bottom: 0.75rem;
	}

	button {
		background: #52EA23;
		color: #000;
		border: none;
		padding: 0.5rem 1rem;
		border-radius: 3px;
		cursor: pointer;
		font-family: inherit;
		font-weight: bold;
		font-size: 0.85rem;
		transition: background 0.2s;
	}

	button:hover {
		background: #45cc1e;
	}
</style>