import { Injectable, OnModuleInit, OnModuleDestroy, Logger } from '@nestjs/common';
import { ConfigService } from '@nestjs/config';
import * as mongoose from 'mongoose';

@Injectable()
export class MongoService implements OnModuleInit, OnModuleDestroy {
  private readonly logger = new Logger(MongoService.name);
  private gameBundleCollection!: mongoose.mongo.Collection<any>;

  constructor(private configService: ConfigService) {}

  async onModuleInit() {
    const mongoUrl = this.configService.get<string>(
      'MONGO_URL',
      'mongodb://localhost:27017/playforge',
    );

    try {
      const connection = await mongoose.connect(mongoUrl);
      this.logger.log('MongoDB connected successfully');
      this.initializeSchemas(connection.connection.db);
    } catch (error) {
      this.logger.error(`MongoDB connection failed: ${error.message}`);
      throw error;
    }
  }

  async onModuleDestroy() {
    await mongoose.connection.close();
    this.logger.log('MongoDB connection closed');
  }

  private initializeSchemas(db: mongoose.mongo.Db) {
    this.gameBundleCollection = db.collection('game_bundles');
  }

  private normalizeStrategy(strategy?: string): string {
    if (strategy === 'template' || strategy === 'hybrid') {
      return strategy;
    }

    return 'full_generation';
  }

  private toInt32(value?: number | null): mongoose.mongo.Int32 | undefined {
    if (value === undefined || value === null || Number.isNaN(Number(value))) {
      return undefined;
    }

    return new mongoose.mongo.Int32(Number(value));
  }

  private compactObject<T extends Record<string, unknown>>(obj: T): Partial<T> {
    return Object.fromEntries(
      Object.entries(obj).filter(([, value]) => value !== undefined && value !== null),
    ) as Partial<T>;
  }

  private toBundleDoc(doc: {
    gameId: string;
    version?: number;
    htmlCode: string;
    cssCode?: string;
    jsCode?: string;
    metadata?: any;
    previewUrl?: string;
  }): Record<string, unknown> {
    const metadata = doc.metadata || {};
    const spec = metadata.gameSpec;
    const generationMeta = this.compactObject({
      strategy: this.normalizeStrategy(metadata.strategy),
      template_id: metadata.templateId ?? undefined,
      model: metadata.model ?? undefined,
      tokens_used: this.toInt32(metadata.tokensUsed),
      generation_time_ms: this.toInt32(metadata.genTimeMs ?? metadata.generationTimeMs ?? 0),
      qa_passed: Boolean(metadata.qaPassed ?? metadata.qa_passed ?? false),
      qa_retries: this.toInt32(metadata.qaRetries ?? metadata.qa_retries ?? 0),
    });

    return this.compactObject({
      game_id: doc.gameId,
      version: this.toInt32(doc.version ?? 1),
      html_code: doc.htmlCode,
      css_code: doc.cssCode ?? '',
      js_code: doc.jsCode ?? '',
      spec,
      ai_conversation: metadata.aiConversation ?? [],
      generation_meta: generationMeta,
      code_size_bytes: this.toInt32(
        metadata.codeSizeBytes ?? Buffer.byteLength(doc.htmlCode, 'utf8'),
      ),
      preview_url: doc.previewUrl ?? null,
      metadata,
      created_at: new Date(),
      updated_at: new Date(),
    });
  }

  private fromBundleDoc(doc: any): any | null {
    if (!doc) {
      return null;
    }

    return {
      id: doc._id?.toString(),
      gameId: doc.game_id,
      version: Number(doc.version ?? 1),
      htmlCode: doc.html_code,
      cssCode: doc.css_code ?? '',
      jsCode: doc.js_code ?? '',
      metadata: doc.metadata ?? {},
      generatedAt: doc.created_at,
      previewUrl: doc.preview_url ?? null,
      createdAt: doc.created_at,
      updatedAt: doc.updated_at,
    };
  }

  async saveBundleDoc(doc: {
    gameId: string;
    version?: number;
    htmlCode: string;
    cssCode?: string;
    jsCode?: string;
    metadata?: any;
    previewUrl?: string;
  }): Promise<any> {
    try {
      const bundleDoc = this.toBundleDoc(doc);
      const result = await this.gameBundleCollection.insertOne(bundleDoc);

      return this.fromBundleDoc(
        await this.gameBundleCollection.findOne({ _id: result.insertedId }),
      );
    } catch (error) {
      this.logger.error(
        `Failed to save bundle: ${error.message}${error.errInfo ? ` ${JSON.stringify(error.errInfo)}` : ''}`,
      );
      throw error;
    }
  }

  async getBundleByGameId(
    gameId: string,
    version?: number,
  ): Promise<any | null> {
    try {
      if (version) {
        return this.fromBundleDoc(
          await this.gameBundleCollection.findOne({
            game_id: gameId,
            version: Number(version),
          }),
        );
      }

      const docs = await this.gameBundleCollection
        .find({ game_id: gameId })
        .sort({ version: -1 })
        .limit(1)
        .toArray();

      return this.fromBundleDoc(docs[0]);
    } catch (error) {
      this.logger.error(`Failed to get bundle: ${error.message}`);
      throw error;
    }
  }

  async getBundleHistory(gameId: string): Promise<any[]> {
    try {
      const docs = await this.gameBundleCollection
        .find({ game_id: gameId })
        .sort({ version: 1 })
        .toArray();

      return docs.map((doc) => this.fromBundleDoc(doc));
    } catch (error) {
      this.logger.error(`Failed to get bundle history: ${error.message}`);
      throw error;
    }
  }

  async updateBundleMetadata(
    gameId: string,
    version: number,
    metadata: any,
  ): Promise<any> {
    try {
      await this.gameBundleCollection.updateOne(
        { game_id: gameId, version: Number(version) },
        {
          $set: {
            metadata,
            updated_at: new Date(),
          },
        },
      );

      return this.getBundleByGameId(gameId, version);
    } catch (error) {
      this.logger.error(`Failed to update bundle metadata: ${error.message}`);
      throw error;
    }
  }

  async deleteBundlesByGameId(gameId: string): Promise<any> {
    try {
      return await this.gameBundleCollection.deleteMany({ game_id: gameId });
    } catch (error) {
      this.logger.error(`Failed to delete bundles: ${error.message}`);
      throw error;
    }
  }
}
