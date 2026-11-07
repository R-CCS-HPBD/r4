import {
	IAuthenticateGeneric,
	ICredentialType,
	INodeProperties,
} from 'n8n-workflow';

/**
 * RiVault API Credentials
 *
 * Used by all RiVault n8n nodes to authenticate with the Gateway.
 */
export class RiVaultApi implements ICredentialType {
	name = 'rivaultApi';
	displayName = 'RiVault API';
	documentationUrl = 'https://github.com/R-CCS-HPBD/r4/tree/main/n8n/nodes';

	properties: INodeProperties[] = [
		{
			displayName: 'Gateway URL',
			name: 'gatewayUrl',
			type: 'string',
			default: 'http://gateway:8000',
			placeholder: 'http://gateway:8000',
			description: 'The base URL of the RiVault Gateway',
		},
		{
			displayName: 'API Key',
			name: 'apiKey',
			type: 'string',
			typeOptions: {
				password: true,
			},
			default: '',
			description: 'Your RiVault API key',
		},
		{
			displayName: 'LiteLLM Master Key',
			name: 'litellmKey',
			type: 'string',
			typeOptions: {
				password: true,
			},
			default: '',
			description: 'LiteLLM master key for direct LLM access',
		},
	];

	authenticate: IAuthenticateGeneric = {
		type: 'generic',
		properties: {
			headers: {
				Authorization: '=Bearer {{$credentials.apiKey}}',
			},
		},
	};
}
