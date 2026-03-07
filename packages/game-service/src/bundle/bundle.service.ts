import { Injectable, Logger } from '@nestjs/common';
import { MongoService } from '../mongo/mongo.service';

@Injectable()
export class BundleService {
  private readonly logger = new Logger(BundleService.name);

  constructor(private mongoService: MongoService) {}

  async saveBundle(bundleData: {
    gameId: string;
    version?: number;
    htmlCode: string;
    cssCode?: string;
    jsCode?: string;
    metadata?: any;
    previewUrl?: string;
  }): Promise<any> {
    try {
      return await this.mongoService.saveBundleDoc(bundleData);
    } catch (error) {
      this.logger.error(`Failed to save bundle: ${error.message}`);
      throw error;
    }
  }

  async getBundle(gameId: string, version?: number): Promise<any | null> {
    try {
      return await this.mongoService.getBundleByGameId(gameId, version);
    } catch (error) {
      this.logger.error(`Failed to get bundle: ${error.message}`);
      throw error;
    }
  }

  async getBundleHistory(gameId: string): Promise<any[]> {
    try {
      return await this.mongoService.getBundleHistory(gameId);
    } catch (error) {
      this.logger.error(`Failed to get bundle history: ${error.message}`);
      throw error;
    }
  }

  async getLatestBundle(gameId: string): Promise<any | null> {
    try {
      return await this.mongoService.getBundleByGameId(gameId);
    } catch (error) {
      this.logger.error(`Failed to get latest bundle: ${error.message}`);
      throw error;
    }
  }
}
