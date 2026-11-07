import {
	IExecuteFunctions,
	INodeExecutionData,
	INodeType,
	INodeTypeDescription,
	NodeOperationError,
} from 'n8n-workflow';

export class RiVaultUpload implements INodeType {
	description: INodeTypeDescription = {
		displayName: 'RiVault Upload',
		name: 'riVaultUpload',
		icon: 'file:rivault.svg',
		group: ['transform'],
		version: 1,
		subtitle: '={{$parameter["operation"]}}',
		description: 'Upload files to RiVault storage',
		defaults: {
			name: 'RiVault Upload',
		},
		inputs: ['main'],
		outputs: ['main'],
		credentials: [
			{
				name: 'riVaultApi',
				required: true,
			},
		],
		properties: [
			{
				displayName: 'Operation',
				name: 'operation',
				type: 'options',
				noDataExpression: true,
				options: [
					{
						name: 'Upload',
						value: 'upload',
						description: 'Upload a file and get presigned URL',
						action: 'Upload file to RiVault',
					},
					{
						name: 'Get Presigned URL',
						value: 'getPresignedUrl',
						description: 'Get a presigned URL for existing file',
						action: 'Get presigned URL for file',
					},
					{
						name: 'List Files',
						value: 'listFiles',
						description: 'List uploaded files',
						action: 'List uploaded files',
					},
					{
						name: 'Delete',
						value: 'delete',
						description: 'Delete a file',
						action: 'Delete file from RiVault',
					},
				],
				default: 'upload',
			},
			// Upload operation
			{
				displayName: 'Binary Property',
				name: 'binaryPropertyName',
				type: 'string',
				default: 'data',
				displayOptions: {
					show: {
						operation: ['upload'],
					},
				},
				description: 'Name of the binary property containing the file to upload',
			},
			{
				displayName: 'Project ID',
				name: 'projectId',
				type: 'string',
				default: '',
				displayOptions: {
					show: {
						operation: ['upload', 'listFiles'],
					},
				},
				description: 'Project to associate the file with',
			},
			{
				displayName: 'File Path',
				name: 'filePath',
				type: 'string',
				default: '',
				displayOptions: {
					show: {
						operation: ['getPresignedUrl', 'delete'],
					},
				},
				required: true,
				description: 'Path to the file in storage',
			},
			{
				displayName: 'TTL (Seconds)',
				name: 'ttl',
				type: 'number',
				default: 3600,
				displayOptions: {
					show: {
						operation: ['getPresignedUrl'],
					},
				},
				description: 'Time to live for the presigned URL',
			},
			{
				displayName: 'Options',
				name: 'options',
				type: 'collection',
				placeholder: 'Add Option',
				default: {},
				options: [
					{
						displayName: 'Content Type',
						name: 'contentType',
						type: 'string',
						default: '',
						description: 'MIME type of the file',
					},
					{
						displayName: 'Tags',
						name: 'tags',
						type: 'json',
						default: '{}',
						description: 'Tags to associate with the file',
					},
					{
						displayName: 'Overwrite',
						name: 'overwrite',
						type: 'boolean',
						default: false,
						description: 'Whether to overwrite existing file',
					},
				],
			},
		],
	};

	async execute(this: IExecuteFunctions): Promise<INodeExecutionData[][]> {
		const items = this.getInputData();
		const returnData: INodeExecutionData[] = [];
		const credentials = await this.getCredentials('riVaultApi');
		const operation = this.getNodeParameter('operation', 0) as string;

		const baseUrl = credentials.baseUrl as string;

		for (let i = 0; i < items.length; i++) {
			try {
				let response;

				if (operation === 'upload') {
					const binaryPropertyName = this.getNodeParameter('binaryPropertyName', i) as string;
					const projectId = this.getNodeParameter('projectId', i) as string;
					const options = this.getNodeParameter('options', i) as {
						contentType?: string;
						tags?: string;
						overwrite?: boolean;
					};

					const binaryData = this.helpers.assertBinaryData(i, binaryPropertyName);
					const buffer = await this.helpers.getBinaryDataBuffer(i, binaryPropertyName);

					// Upload file via multipart form
					response = await this.helpers.httpRequest({
						method: 'POST',
						url: `${baseUrl}/gw/files/upload`,
						headers: {
							'Authorization': `Bearer ${credentials.apiKey}`,
						},
						formData: {
							file: {
								value: buffer,
								options: {
									filename: binaryData.fileName || 'file',
									contentType: options.contentType || binaryData.mimeType,
								},
							},
							project_id: projectId,
							overwrite: String(options.overwrite || false),
							tags: options.tags || '{}',
						},
					});
				} else if (operation === 'getPresignedUrl') {
					const filePath = this.getNodeParameter('filePath', i) as string;
					const ttl = this.getNodeParameter('ttl', i) as number;

					response = await this.helpers.httpRequest({
						method: 'POST',
						url: `${baseUrl}/gw/files/presigned-url`,
						headers: {
							'Authorization': `Bearer ${credentials.apiKey}`,
							'Content-Type': 'application/json',
						},
						body: {
							file_path: filePath,
							ttl_seconds: ttl,
						},
					});
				} else if (operation === 'listFiles') {
					const projectId = this.getNodeParameter('projectId', i) as string;

					const queryParams = projectId ? `?project_id=${projectId}` : '';

					response = await this.helpers.httpRequest({
						method: 'GET',
						url: `${baseUrl}/gw/files${queryParams}`,
						headers: {
							'Authorization': `Bearer ${credentials.apiKey}`,
						},
					});
				} else if (operation === 'delete') {
					const filePath = this.getNodeParameter('filePath', i) as string;

					response = await this.helpers.httpRequest({
						method: 'DELETE',
						url: `${baseUrl}/gw/files`,
						headers: {
							'Authorization': `Bearer ${credentials.apiKey}`,
							'Content-Type': 'application/json',
						},
						body: {
							file_path: filePath,
						},
					});
				}

				returnData.push({
					json: response,
					pairedItem: { item: i },
				});
			} catch (error) {
				if (this.continueOnFail()) {
					returnData.push({
						json: { error: (error as Error).message },
						pairedItem: { item: i },
					});
					continue;
				}
				throw new NodeOperationError(this.getNode(), error as Error, { itemIndex: i });
			}
		}

		return [returnData];
	}
}
